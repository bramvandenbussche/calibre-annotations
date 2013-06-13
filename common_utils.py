#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2013, Greg Riker <griker@hotmail.com>'
__docformat__ = 'restructuredtext en'

import re, os, shutil, sys, tempfile, time, urlparse, zipfile
from collections import defaultdict
from time import sleep

from calibre.constants import iswindows
from calibre.ebooks.BeautifulSoup import BeautifulSoup
from calibre.ebooks.metadata import MetaInformation
from calibre.gui2 import Application
from calibre.gui2.dialogs.message_box import MessageBox
from calibre.utils.config import config_dir
from calibre.utils.ipc import RC
from calibre.utils.logging import Log

from calibre_plugins.annotations.message_box_ui import Ui_Dialog, COVER_ICON_SIZE
from calibre_plugins.annotations.reader_app_support import ReaderApp

from PyQt4.Qt import (Qt, QAction, QApplication,
    QCheckBox, QComboBox, QDial, QDialog, QDialogButtonBox, QDoubleSpinBox, QIcon,
    QKeySequence, QLabel, QLineEdit, QMenu, QPixmap, QProgressBar, QPlainTextEdit,
    QRadioButton, QSize, QSizePolicy, QSlider, QSpinBox, QString, QThread, QUrl,
    QVBoxLayout,
    SIGNAL)
from PyQt4.QtWebKit import QWebView

# Stateful controls: (<class>,<list_name>,<get_method>,<default>,<set_method(s)>)
# multiple set_methods are chained, i.e. the results of the first call are passed to the second
# Currently a max of two chained CONTROL_SET methods are implemented, explicity for comboBox
CONTROLS = [
            (QCheckBox, 'checkBox_controls', 'isChecked', False, 'setChecked'),
            (QComboBox, 'comboBox_controls', 'currentText', '', ('findText', 'setCurrentIndex')),
            (QDial, 'dial_controls', 'value', 0, 'setValue'),
            (QDoubleSpinBox, 'doubleSpinBox_controls', 'value', 0, 'setValue'),
            (QLineEdit, 'lineEdit_controls', 'text', '', 'setText'),
            (QRadioButton, 'radioButton_controls', 'isChecked', False, 'setChecked'),
            (QSlider, 'slider_controls', 'value', 0, 'setValue'),
            (QSpinBox, 'spinBox_controls', 'value', 0, 'setValue'),
           ]

CONTROL_CLASSES = [control[0] for control in CONTROLS]
CONTROL_TYPES = [control[1] for control in CONTROLS]
CONTROL_GET = [control[2] for control in CONTROLS]
CONTROL_DEFAULT = [control[3] for control in CONTROLS]
CONTROL_SET = [control[4] for control in CONTROLS]

plugin_tmpdir = 'calibre_annotations_plugin'

plugin_icon_resources = {}

'''     Base classes    '''

class Struct(dict):
    """
    Create an object with dot-referenced members or dictionary
    """
    def __init__(self, **kwds):
        dict.__init__(self, kwds)
        self.__dict__ = self

    def __repr__(self):
        return '\n'.join([" %s: %s" % (key, repr(self[key])) for key in sorted(self.keys())])


class AnnotationStruct(Struct):
    """
    Populate an empty annotation structure with fields for all possible values
    """
    def __init__(self):
        super(AnnotationStruct, self).__init__(
            annotation_id=None,
            book_id=None,
            epubcfi=None,
            genre=None,
            highlight_color=None,
            highlight_text=None,
            last_modification=None,
            location=None,
            location_sort=None,
            note_text=None,
            reader=None,
            )


class BookStruct(Struct):
    """
    Populate an empty book structure with fields for all possible values
    """
    def __init__(self):
        super(BookStruct, self).__init__(
            active=None,
            author=None,
            author_sort=None,
            book_id=None,
            genre='',
            last_annotation=None,
            path=None,
            title=None,
            title_sort=None,
            uuid=None
            )


class DebugLog(Log):

    def __init__(self, plugin_name, level=1):
        #Log.__init__(self, level)
        super(DebugLog, self).__init__(level)
        self.stream = sys.stdout
        self.debug_fn = "%s_DEBUG.txt" % plugin_name
        self.log_to_console = False
        self.out_fs = os.path.join(tempfile.gettempdir(), plugin_tmpdir, self.debug_fn)
        if not os.path.exists(os.path.dirname(self.out_fs)):
            os.makedirs(os.path.dirname(self.out_fs))
        self.out_file = open(self.out_fs, 'w')
        # Write a UTF-8 BOM
        self.out_file.write('\xef\xbb\xbf')
        self.out_file.write('\n')
        self.errors = False

    def __getattr__(self, attr):
        if hasattr(self, attr):
            return getattr(self, attr)
        else:
            return getattr(self.stream, attr)

    def close(self):
        self.out_file.flush()
        self.out_file.close()
        sys.stderr = sys.__stderr__

    def save_log(self, dest_dir):
        self.close()
        src = self.out_fs
        dst = dest_dir
        debug_fs = os.path.join(dest_dir, self.debug_fn)
        if os.path.exists(debug_fs):
            os.remove(debug_fs)
        shutil.copy(src, dst)

    def prints(self, level, *args, **kwargs):
        if self.log_to_console:
            Log.prints(self, level, *args, **kwargs)
        self.out_file.write('%s\n' % args[0])
        self.out_file.flush()

    def write(self, *args):
        '''
        For traceback(), hooked calls to sys.stderr
        '''
        if args[0].strip():
            if 'BeautifulSoup' in args[0] and 'UnicodeWarning:' in args[0]:
                #Log.prints(self, 1, " ignoring BeautifulSoup UnicodeWarning")
                return

            err_type = '<stderr>'
            if 'Traceback' in args[0]:
                err_type = 'Traceback'
                error_report = '{:~^80}\n'.format('  %s   ' % err_type) + \
                               '%s' % args[0].strip()
            elif 'Error' in args[0]:
                error_report = '%s' % args[0].strip() + \
                               '\n{:~^80}'.format('')
            else:
                error_report = '%s' % args[0].strip()
            Log.prints(self, 1, error_report)
            self.out_file.write(error_report + '\n')
            #Log.prints(self, 1, '\n')
            self.errors = True


class PlainTextEdit(QPlainTextEdit):
    """
    Subclass enabling drag 'n drop
    """
    def __init__(self, parent):
        QPlainTextEdit.__init__(self, parent.gui)
        self.parent = parent
        self.opts = parent.opts
        self.log = parent.opts.log
        self.log_location = parent.opts.log_location
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("text/uri-list"):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("text/uri-list"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        data = event.mimeData()
        mime = "text/uri-list"
        url = str(data.data(mime))
        path = urlparse.urlparse(url).path.strip()
        scheme = urlparse.urlparse(url).scheme
        path = re.sub('%20', ' ', path)
        if iswindows:
            if path.startswith('/Shared Folders'):
                path = re.sub(r'\/Shared Folders', 'Z:', path)
            elif path.startswith('/'):
                path = path[1:]
        extension = path.rpartition('.')[2]
        if scheme == 'file' and extension in ['mrv', 'mrvi', 'txt']:
            with open(path) as f:
                raw = f.read()
                u = unicode(raw, 'utf-8')
            self.setPlainText(u)
        else:
            self.log_location("unsupported import: %s" % path)


class Profiler():
    def __init__(self, log, plugin_dir):
        self.log = log
        self.plugin_dir = plugin_dir

    def get_location(self, args=None):
        fn = sys._getframe(1).f_code.co_filename
        if fn.startswith(self.plugin_dir):
            fn = fn[len(self.plugin_dir) + 1:]
        mn = sys._getframe(1).f_code.co_name
        ans = "%s:%s(%s)" % (fn, mn, args if args else '')
        return ans

    def log_location(self, args):
        fn = sys._getframe(2).f_code.co_filename
        if fn.startswith(self.plugin_dir):
            fn = fn[len(self.plugin_dir) + 1:]
        mn = sys._getframe(2).f_code.co_name
        args = [str(item) for item in args]
        ans = "%s:%s(%s)" % (fn, mn, ', '.join(args) if args else '')
        self.log.info(ans)
        return ans

    def null(self, *args):
        pass

    def where_am_i(self, *args):
        self.log_location(args)

    def what_time_is_it(self, location):
        ans = '{:-^120}'.format('   %s @ %s   ' % (location, time.strftime('%H:%M:%S')))
        self.log.info(ans)
        return ans


class SizePersistedDialog(QDialog):
    '''
    This dialog is a base class for any dialogs that want their size/position
    restored when they are next opened.
    '''
    def __init__(self, parent, unique_pref_name):
        QDialog.__init__(self, parent, Qt.WindowStaysOnTopHint)
        self.unique_pref_name = unique_pref_name
        self.geom = self.prefs.get(unique_pref_name, None)
        self.finished.connect(self.dialog_closing)

    def resize_dialog(self):
        if self.geom is None:
            self.resize(self.sizeHint())
        else:
            self.restoreGeometry(self.geom)

    def dialog_closing(self, result):
        geom = bytearray(self.saveGeometry())
        self.prefs.set(self.unique_pref_name, geom)


'''     Exceptions      '''

class AnnotationsException(Exception):
    ''' '''
    pass


class DeviceNotMountedException(Exception):
    ''' '''
    pass


class ExpiredException(Exception):
    pass


class UnknownAnnotationTypeException(Exception):
    pass


'''     Dialogs         '''

class ImportAnnotationsDialog(QDialog):
    def __init__(self, parent, friendly_name, rac):
        #self.dialog = QDialog(parent.gui)
        QDialog.__init__(self, parent.gui)
        self.parent = parent
        self.opts = parent.opts
        self.rac = rac
        parent_loc = self.parent.gui.pos()
        self.move(parent_loc.x(), parent_loc.y())
        self.setWindowTitle(rac.import_dialog_title)
        self.setWindowIcon(self.opts.icon)

        l = QVBoxLayout()
        self.setLayout(l)

        self.pte = PlainTextEdit(self.parent)
        self.pte.setPlainText(rac.initial_dialog_text)
        self.pte.setMinimumWidth(400)
        l.addWidget(self.pte)

        self.dialogButtonBox = QDialogButtonBox(QDialogButtonBox.Cancel|QDialogButtonBox.Help)
        self.import_button = self.dialogButtonBox.addButton(self.dialogButtonBox.Ok)
        self.import_button.setText('Import')
        self.dialogButtonBox.clicked.connect(self.import_annotations_dialog_clicked)
        l.addWidget(self.dialogButtonBox)

        self.rejected.connect(self.close)

        self.exec_()
        self.text = str(self.pte.toPlainText())

    def close(self):
        # Catch ESC and close button
        self.pte.setPlainText('')
        self.accept()

    def import_annotations_dialog_clicked(self, button):
        BUTTON_ROLES = ['AcceptRole', 'RejectRole', 'DestructiveRole', 'ActionRole',
                        'HelpRole', 'YesRole', 'NoRole', 'ApplyRole', 'ResetRole']
        if self.dialogButtonBox.buttonRole(button) == QDialogButtonBox.AcceptRole:
            # Remove initial_dialog_text if user clicks OK without dropping file
            if self.text() == self.rac.initial_dialog_text:
                self.pte.clear()
            self.accept()
        elif self.dialogButtonBox.buttonRole(button) == QDialogButtonBox.HelpRole:
            hv = HelpView(self, self.opts.icon, self.opts.prefs, html=self.rac.import_help_text)
            hv.show()
        else:
            self.close()

    def text(self):
        return unicode(self.pte.toPlainText())


class CoverMessageBox(QDialog, Ui_Dialog):

    ERROR = 0
    WARNING = 1
    INFO = 2
    QUESTION = 3

    def __init__(self, type_, title, msg, opts,
                 det_msg='',
                 q_icon=None,
                 show_copy_button=True,
                 parent=None, default_yes=True):
        QDialog.__init__(self, parent)

        if q_icon is None:
            icon = {
                    self.ERROR: 'error',
                    self.WARNING: 'warning',
                    self.INFO:    'information',
                    self.QUESTION: 'question',
            }[type_]
            icon = 'dialog_%s.png' % icon
            self.icon = QIcon(I(icon))
        else:
            self.icon = q_icon
        self.setupUi(self)

        self.setWindowTitle(title)
        self.setWindowIcon(opts.icon)
        #self.icon_label.setPixmap(self.icon.pixmap(self.COVER_SIZE, self.COVER_SIZE))
        self.icon_label.setPixmap(self.icon.pixmap(COVER_ICON_SIZE))
        self.msg.setText(msg)
        self.msg.setOpenExternalLinks(True)

        self.det_msg.setPlainText(det_msg)
        self.det_msg.setVisible(False)
        self.toggle_checkbox.setVisible(False)

        if show_copy_button:
            self.ctc_button = self.bb.addButton(_('&Copy to clipboard'),
                    self.bb.ActionRole)
            self.ctc_button.clicked.connect(self.copy_to_clipboard)

        self.show_det_msg = _('Show &details')
        self.hide_det_msg = _('Hide &details')
        self.det_msg_toggle = self.bb.addButton(self.show_det_msg, self.bb.ActionRole)
        self.det_msg_toggle.clicked.connect(self.toggle_det_msg)
        self.det_msg_toggle.setToolTip(
                _('Show detailed information'))

        self.copy_action = QAction(self)
        self.addAction(self.copy_action)
        self.copy_action.setShortcuts(QKeySequence.Copy)
        self.copy_action.triggered.connect(self.copy_to_clipboard)

        self.is_question = type_ == self.QUESTION
        if self.is_question:
            self.bb.setStandardButtons(self.bb.Yes | self.bb.No)
            self.bb.button(self.bb.Yes if default_yes else self.bb.No
                    ).setDefault(True)
            self.default_yes = default_yes
        else:
            self.bb.button(self.bb.Ok).setDefault(True)

        if not det_msg:
            self.det_msg_toggle.setVisible(False)

        self.do_resize()

    def toggle_det_msg(self, *args):
        vis = unicode(self.det_msg_toggle.text()) == self.hide_det_msg
        self.det_msg_toggle.setText(self.show_det_msg if vis else
                self.hide_det_msg)
        self.det_msg.setVisible(not vis)
        self.do_resize()

    def do_resize(self):
        sz = self.sizeHint() + QSize(100, 0)
        sz.setWidth(min(500, sz.width()))
        sz.setHeight(min(500, sz.height()))
        self.resize(sz)

    def copy_to_clipboard(self, *args):
        QApplication.clipboard().setText(
                'calibre, version %s\n%s: %s\n\n%s' %
                (__version__, unicode(self.windowTitle()),
                    unicode(self.msg.text()),
                    unicode(self.det_msg.toPlainText())))
        if hasattr(self, 'ctc_button'):
            self.ctc_button.setText(_('Copied'))

    def showEvent(self, ev):
        ret = QDialog.showEvent(self, ev)
        if self.is_question:
            try:
                self.bb.button(self.bb.Yes if self.default_yes else self.bb.No
                        ).setFocus(Qt.OtherFocusReason)
            except:
                # Buttons were changed
                pass
        else:
            self.bb.button(self.bb.Ok).setFocus(Qt.OtherFocusReason)
        return ret

    def set_details(self, msg):
        if not msg:
            msg = ''
        self.det_msg.setPlainText(msg)
        self.det_msg_toggle.setText(self.show_det_msg)
        self.det_msg_toggle.setVisible(bool(msg))
        self.det_msg.setVisible(False)
        self.do_resize()


class HelpView(SizePersistedDialog):
    '''
    Modeless dialog for presenting HTML help content
    '''

    def __init__(self, parent, icon, prefs, html=None, page=None, title=''):
        self.prefs = prefs
        #QDialog.__init__(self, parent=parent)
        super(HelpView, self).__init__(parent, 'help_dialog')
        self.setWindowTitle(title)
        self.setWindowIcon(icon)
        self.l = QVBoxLayout(self)
        self.setLayout(self.l)

        self.wv = QWebView()
        if html is not None:
            self.wv.setHtml(html)
        elif page is not None:
            self.wv.load(QUrl(page))
        self.wv.setMinimumHeight(100)
        self.wv.setMaximumHeight(16777215)
        self.wv.setMinimumWidth(400)
        self.wv.setMaximumWidth(16777215)
        self.wv.setGeometry(0, 0, 400, 100)
        self.wv.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.l.addWidget(self.wv)

        # Sizing
        sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.sizePolicy().hasHeightForWidth())
        self.setSizePolicy(sizePolicy)
        self.resize_dialog()


class ProgressBar(QDialog):
    def __init__(self, parent=None, max_items=100, window_title='Progress Bar',
                 label='Label goes here', on_top=False):
        if on_top:
            QDialog.__init__(self, parent=parent, flags=Qt.WindowStaysOnTopHint)
        else:
            QDialog.__init__(self, parent=parent)
        self.application = Application
        self.setWindowTitle(window_title)
        self.l = QVBoxLayout(self)
        self.setLayout(self.l)

        self.label = QLabel(label)
        self.label.setAlignment(Qt.AlignHCenter)
        self.l.addWidget(self.label)

        self.progressBar = QProgressBar(self)
        self.progressBar.setRange(0, max_items)
        self.progressBar.setValue(0)
        self.l.addWidget(self.progressBar)

    def increment(self):
        self.progressBar.setValue(self.progressBar.value() + 1)
        self.refresh()

    def refresh(self):
        self.application.processEvents()

    def set_label(self, value):
        self.label.setText(value)
        self.refresh()

    def set_maximum(self, value):
        self.progressBar.setMaximum(value)
        self.refresh()

    def set_value(self, value):
        self.progressBar.setValue(value)
        self.refresh()


'''     Threads         '''

class IndexLibrary(QThread):
    '''
    Build two indexes of library
    {uuid: {'title':..., 'author':...}}
    {'title': {'uuid':..., 'author':...}}
    '''

    def __init__(self, parent):
        QThread.__init__(self, parent)
        self.signal = SIGNAL("library_index_complete")
        self.cdb = parent.opts.gui.current_db
        self.title_map = None
        self.uuid_map = None

    def run(self):
        self.title_map = self.index_by_title()
        self.uuid_map = self.index_by_uuid()
        self.emit(self.signal)

    def index_by_title(self):
        id = self.cdb.FIELD_MAP['id']
        uuid = self.cdb.FIELD_MAP['uuid']
        title = self.cdb.FIELD_MAP['title']
        authors = self.cdb.FIELD_MAP['authors']

        by_title = {}
        for record in self.cdb.data.iterall():
            by_title[record[title]] = {
                'authors': record[authors].split(','),
                'id': record[id],
                'uuid': record[uuid],
                }
        return by_title

    def index_by_uuid(self):
        id = self.cdb.FIELD_MAP['id']
        uuid = self.cdb.FIELD_MAP['uuid']
        title = self.cdb.FIELD_MAP['title']
        authors = self.cdb.FIELD_MAP['authors']

        by_uuid = {}
        for record in self.cdb.data.iterall():
            by_uuid[record[uuid]] = {
                'authors': record[authors].split(','),
                'id': record[id],
                'title': record[title],
                }
        return by_uuid


'''     Helper functions   '''


def existing_annotations(parent, field, return_all=False):
    '''
    Return count of existing annotations, or existence of any
    '''
    import calibre_plugins.annotations.config as cfg
    annotation_map = []
    if field:
        db = parent.opts.gui.current_db
        id = db.FIELD_MAP['id']
        for i, record in enumerate(db.data.iterall()):
            mi = db.get_metadata(record[id], index_is_id=True)
            if field == 'Comments':
                if mi.comments:
                    soup = BeautifulSoup(mi.comments)
                else:
                    continue
            else:
                soup = BeautifulSoup(mi.get_user_metadata(field, False)['#value#'])
            if soup.find('div', 'user_annotations') is not None:
                annotation_map.append(mi.id)
                if not return_all:
                    break
        if return_all:
            parent.log_location("Identified %d annotated books of %d total books" %
                (len(annotation_map), len(db.data)))
        return annotation_map


def get_clippings_cid(parent, title):
    '''
    Find or create cid for title
    '''
    cid = None
    try:
        cid = list(parent.opts.gui.current_db.data.parse('title:"%s" and tag:Clippings' % title))[0]
    except:
        mi = MetaInformation(title, authors = ['Various'])
        mi.tags = ['Clippings']
        cid = parent.opts.gui.current_db.create_book_entry(mi, cover=None,
            add_duplicates=False, force_id=None)
    return cid


def get_icon(icon_name):
    '''
    Retrieve a QIcon for the named image from the zip file if it exists,
    or if not then from Calibre's image cache.
    '''
    if icon_name:
        pixmap = get_pixmap(icon_name)
        if pixmap is None:
            # Look in Calibre's cache for the icon
            return QIcon(I(icon_name))
        else:
            return QIcon(pixmap)
    return QIcon()


def get_local_images_dir(subfolder=None):
    '''
    Returns a path to the user's local resources/images folder
    If a subfolder name parameter is specified, appends this to the path
    '''
    images_dir = os.path.join(config_dir, 'resources/images')
    if subfolder:
        images_dir = os.path.join(images_dir, subfolder)
    if iswindows:
        images_dir = os.path.normpath(images_dir)
    return images_dir


def get_pixmap(icon_name):
    '''
    Retrieve a QPixmap for the named image
    Any icons belonging to the plugin must be prefixed with 'images/'
    '''
    global plugin_icon_resources, plugin_name

    if not icon_name.startswith('images/'):
        # We know this is definitely not an icon belonging to this plugin
        pixmap = QPixmap()
        pixmap.load(I(icon_name))
        return pixmap

    # Check to see whether the icon exists as a Calibre resource
    # This will enable skinning if the user stores icons within a folder like:
    # ...\AppData\Roaming\calibre\resources\images\Plugin Name\
    if plugin_name:
        local_images_dir = get_local_images_dir(plugin_name)
        local_image_path = os.path.join(local_images_dir, icon_name.replace('images/', ''))
        if os.path.exists(local_image_path):
            pixmap = QPixmap()
            pixmap.load(local_image_path)
            return pixmap

    # As we did not find an icon elsewhere, look within our zip resources
    if icon_name in plugin_icon_resources:
        pixmap = QPixmap()
        pixmap.loadFromData(plugin_icon_resources[icon_name])
        return pixmap
    return None


def get_resource_files(path, folder=None):
    namelist = []
    with zipfile.ZipFile(path) as zf:
        namelist = zf.namelist()
    if folder and folder.endswith('/'):
        namelist = [item for item in namelist if item.startswith(folder) and item > folder]
    return namelist


def get_selected_book_mi(opts, msg=None, det_msg=None):
    # Get currently selected books
    rows = opts.gui.library_view.selectionModel().selectedRows()

    if len(rows) == 0 or len(rows) > 1:
        MessageBox(MessageBox.WARNING,
                   'Select a book to receive annotations',
                   msg,
                   det_msg=det_msg,
                   show_copy_button=False,
                   parent=opts.gui).exec_()
        return None

    # Get the current metadata for this book from the db
    ids = list(map(opts.gui.library_view.model().id, rows))
    if ids:
        mi = opts.gui.current_db.get_metadata(ids[0], index_is_id=True)
        return mi
    else:
        return None


def inventory_controls(ui, dump_controls=False):
    '''
     Build an inventory of stateful controls
    '''
    controls = {'owner': ui.__class__.__name__}
    control_dict = defaultdict(list)
    for control_type in CONTROL_TYPES:
        control_dict[control_type] = []

    # Inventory existing controls
    for item in ui.__dict__:
        if type(ui.__dict__[item]) in CONTROL_CLASSES:
            index = CONTROL_CLASSES.index(type(ui.__dict__[item]))
            control_dict[CONTROL_TYPES[index]].append(str(ui.__dict__[item].objectName()))

    for control_list in CONTROL_TYPES:
        if control_dict[control_list]:
            controls[control_list] = control_dict[control_list]

    if dump_controls:
        for control_type in CONTROL_TYPES:
            if control_type in controls:
                print("  %s: %s" % (control_type, controls[control_type]))

    return controls


def move_annotations(parent, annotation_map, old_destination_field, new_destination_field,
                     window_title="Moving annotations"):
    '''
    Move annotations from old_destination_field to new_destination_field
    annotation_map precalculated in thread in config.py
    '''
    import calibre_plugins.annotations.config as cfg

    parent.opts.log_location("%s -> %s" % (old_destination_field, new_destination_field))

    db = parent.opts.gui.current_db
    id = db.FIELD_MAP['id']

    # Show progress
    pb = ProgressBar(parent=parent, window_title=window_title, on_top=True)
    total_books = len(annotation_map)
    pb.set_maximum(total_books)
    pb.set_value(1)
    pb.set_label('{:^100}'.format('Moving annotations for %d books' % total_books))
    pb.show()

    transient_db = 'transient'

    # Prepare a new COMMENTS_DIVIDER
    comments_divider = '<div class="comments_divider"><p style="text-align:center;margin:1em 0 1em 0">{0}</p></div>'.format(
        cfg.plugin_prefs.get('COMMENTS_DIVIDER', '&middot;  &middot;  &bull;  &middot;  &#x2726;  &middot;  &bull;  &middot; &middot;'))

    for cid in annotation_map:
        mi = db.get_metadata(cid, index_is_id=True)

        # Comments -> custom
        if old_destination_field == 'Comments' and new_destination_field.startswith('#'):
            if mi.comments:
                old_soup = BeautifulSoup(mi.comments)
                uas = old_soup.find('div', 'user_annotations')
                if uas:
                    # Remove user_annotations from Comments
                    uas.extract()

                    # Remove comments_divider from Comments
                    cd = old_soup.find('div', 'comments_divider')
                    if cd:
                        cd.extract()

                    # Save stripped Comments
                    mi.comments = unicode(old_soup)

                    # Capture content
                    parent.opts.db.capture_content(uas, cid, transient_db)

                    # Regurgitate content with current CSS style
                    new_soup = parent.opts.db.rerender_to_html(transient_db, cid)

                    # Add user_annotations to destination
                    um = mi.metadata_for_field(new_destination_field)
                    um['#value#'] = unicode(new_soup)
                    mi.set_user_metadata(new_destination_field, um)

                    # Update the record with stripped Comments, populated custom field
                    db.set_metadata(cid, mi, set_title=False, set_authors=False,
                                    commit=True, force_changes=True, notify=True)
                    pb.increment()

        # custom -> Comments
        elif old_destination_field.startswith('#') and new_destination_field == 'Comments':
            if mi.get_user_metadata(old_destination_field, False)['#value#'] is not None:
                old_soup = BeautifulSoup(mi.get_user_metadata(old_destination_field, False)['#value#'])
                uas = old_soup.find('div', 'user_annotations')
                if uas:
                    # Remove user_annotations from custom field
                    uas.extract()

                    # Capture content
                    parent.opts.db.capture_content(uas, cid, transient_db)

                    # Regurgitate content with current CSS style
                    new_soup = parent.opts.db.rerender_to_html(transient_db, cid)

                    # Save stripped custom field data
                    um = mi.metadata_for_field(old_destination_field)
                    um['#value#'] = unicode(old_soup)
                    mi.set_user_metadata(old_destination_field, um)

                    # Add user_annotations to Comments
                    if mi.comments is None:
                        mi.comments = unicode(new_soup)
                    else:
                        mi.comments = mi.comments + \
                                      unicode(comments_divider) + \
                                      unicode(new_soup)

                    # Update the record with stripped custom field, updated Comments
                    db.set_metadata(cid, mi, set_title=False, set_authors=False,
                                    commit=True, force_changes=True, notify=True)
                    pb.increment()

        # custom -> custom
        elif old_destination_field.startswith('#') and new_destination_field.startswith('#'):

            if mi.get_user_metadata(old_destination_field, False)['#value#'] is not None:
                old_soup = BeautifulSoup(mi.get_user_metadata(old_destination_field, False)['#value#'])
                uas = old_soup.find('div', 'user_annotations')
                if uas:
                    # Remove user_annotations from originating custom field
                    uas.extract()

                    # Capture content
                    parent.opts.db.capture_content(uas, cid, transient_db)

                    # Regurgitate content with current CSS style
                    new_soup = parent.opts.db.rerender_to_html(transient_db, cid)

                    # Save stripped custom field data
                    um = mi.metadata_for_field(old_destination_field)
                    um['#value#'] = unicode(old_soup)
                    mi.set_user_metadata(old_destination_field, um)

                    # Add new_soup to destination field
                    um = mi.metadata_for_field(new_destination_field)
                    um['#value#'] = unicode(new_soup)
                    mi.set_user_metadata(new_destination_field, um)

                    # Update the record
                    db.set_metadata(cid, mi, set_title=False, set_authors=False,
                                    commit=True, force_changes=True, notify=True)
                    pb.increment()

        # same field -> same field - called from config:configure_appearance()
        elif (old_destination_field == new_destination_field):
            pb.set_label('{:^100}'.format('Updating annotations for %d books' % total_books))

            if new_destination_field == 'Comments':
                if mi.comments:
                    old_soup = BeautifulSoup(mi.comments)
                    uas = old_soup.find('div', 'user_annotations')
                    if uas:
                        # Remove user_annotations from Comments
                        uas.extract()

                        # Remove comments_divider from Comments
                        cd = old_soup.find('div', 'comments_divider')
                        if cd:
                            cd.extract()

                        # Save stripped Comments
                        mi.comments = unicode(old_soup)

                        # Capture content
                        parent.opts.db.capture_content(uas, cid, transient_db)

                        # Regurgitate content with current CSS style
                        new_soup = parent.opts.db.rerender_to_html(transient_db, cid)

                        # Add user_annotations to Comments
                        if mi.comments is None:
                            mi.comments = unicode(new_soup)
                        else:
                            mi.comments = mi.comments + \
                                          unicode(comments_divider) + \
                                          unicode(new_soup)

                        # Update the record with stripped custom field, updated Comments
                        db.set_metadata(cid, mi, set_title=False, set_authors=False,
                                        commit=True, force_changes=True, notify=True)
                        pb.increment()

            else:
                # Update custom field
                old_soup = BeautifulSoup(mi.get_user_metadata(old_destination_field, False)['#value#'])
                uas = old_soup.find('div', 'user_annotations')
                if uas:
                    # Remove user_annotations from originating custom field
                    uas.extract()

                    # Capture content
                    parent.opts.db.capture_content(uas, cid, transient_db)

                    # Regurgitate content with current CSS style
                    new_soup = parent.opts.db.rerender_to_html(transient_db, cid)

                    # Add stripped old_soup plus new_soup to destination field
                    um = mi.metadata_for_field(new_destination_field)
                    um['#value#'] = unicode(old_soup) + unicode(new_soup)
                    mi.set_user_metadata(new_destination_field, um)

                    # Update the record
                    db.set_metadata(cid, mi, set_title=False, set_authors=False,
                                    commit=True, force_changes=True, notify=True)
                    pb.increment()

    # Hide the progress bar
    pb.hide()

    # Change field value to friendly name
    if old_destination_field.startswith('#'):
        for cf in parent.custom_fields:
            if parent.custom_fields[cf]['field'] == old_destination_field:
                old_destination_field = cf
                break
    if new_destination_field.startswith('#'):
        for cf in parent.custom_fields:
            if parent.custom_fields[cf]['field'] == new_destination_field:
                new_destination_field = cf
                break

    # Report what happened
    if old_destination_field == new_destination_field:
        msg = "<p>Annotations updated to new appearance settings for %d {0}.</p>" % len(annotation_map)
    else:
        msg = ("<p>Annotations for %d {0} moved from <b>%s</b> to <b>%s</b>.</p>" %
                (len(annotation_map), old_destination_field, new_destination_field))
    if len(annotation_map) == 1:
        msg = msg.format('book')
    else:
        msg = msg.format('books')
    MessageBox(MessageBox.INFO,
               '',
               msg=msg,
               show_copy_button=False,
               parent=parent.gui).exec_()
    parent.opts.log_location("INFO: %s" % msg)

    # Update the UI
    updateCalibreGUIView()


def restore_state(ui, prefs, restore_position=False):
    if restore_position:
        _restore_ui_position(ui, ui.controls['owner'])

    # Restore stateful controls
    for control_list in ui.controls:
        if control_list == 'owner':
            continue
        index = CONTROL_TYPES.index(control_list)
        for control in ui.controls[control_list]:
            control_ref = getattr(ui, control, None)
            if control_ref is not None:
                if isinstance(CONTROL_SET[index], unicode):
                    setter_ref = getattr(control_ref, CONTROL_SET[index], None)
                    if setter_ref is not None:
                        if callable(setter_ref):
                            setter_ref(prefs.get(control, CONTROL_DEFAULT[index]))
                elif isinstance(CONTROL_SET[index], tuple) and len(CONTROL_SET[index]) == 2:
                    # Special case for comboBox - first findText, then setCurrentIndex
                    setter_ref = getattr(control_ref, CONTROL_SET[index][0], None)
                    if setter_ref is not None:
                        if callable(setter_ref):
                            result = setter_ref(prefs.get(control, CONTROL_DEFAULT[index]))
                            setter_ref = getattr(control_ref, CONTROL_SET[index][1], None)
                            if setter_ref is not None:
                                if callable(setter_ref):
                                    setter_ref(result)
                else:
                    print(" invalid CONTROL_SET tuple for '%s'" % control)
                    print("  maximum of two chained methods")


def _restore_ui_position(ui, owner):
    parent_loc = ui.iap.gui.pos()
    if True:
        last_x = prefs.get('%s_last_x' % owner, parent_loc.x())
        last_y = prefs.get('%s_last_y' % owner, parent_loc.y())
    else:
        last_x = parent_loc.x()
        last_y = parent_loc.y()
    ui.move(last_x, last_y)


def save_state(ui, prefs, save_position=False):
    if save_position:
        _save_ui_position(ui, ui.controls['owner'])

    # Save stateful controls
    for control_list in ui.controls:
        if control_list == 'owner':
            continue
        index = CONTROL_TYPES.index(control_list)

        for control in ui.controls[control_list]:
            # Intercept QString objects, coerce to unicode
            qt_type = getattr(getattr(ui, control), CONTROL_GET[index])()
            if type(qt_type) is QString:
                qt_type = unicode(qt_type)
            prefs.set(control, qt_type)


def _save_ui_position(ui, owner):
    prefs.set('%s_last_x' % owner, ui.pos().x())
    prefs.set('%s_last_y' % owner, ui.pos().y())


def set_plugin_icon_resources(name, resources):
    '''
    Set our global store of plugin name and icon resources for sharing between
    the InterfaceAction class which reads them and the ConfigWidget
    if needed for use on the customization dialog for this plugin.
    '''
    global plugin_icon_resources, plugin_name
    plugin_name = name
    plugin_icon_resources = resources


def updateCalibreGUIView():
    '''
    Refresh the GUI view
    '''
    t = RC(print_error=False)
    t.start()
    sleep(0.5)
    while True:
        if t.done:
            t.conn.send('refreshdb:')
            t.conn.close()
            break
        sleep(0.5)
