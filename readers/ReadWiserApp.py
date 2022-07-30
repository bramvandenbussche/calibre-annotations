#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2022, Bram Vandenbussche <bram@vandenbussche.me>'
__docformat__ = 'restructuredtext en'

import datetime, time

import ssl
from urllib.request import Request, urlopen
import urllib.parse
import json

from calibre_plugins.annotations.common_utils import (AnnotationStruct, BookStruct)
from calibre_plugins.annotations.reader_app_support import ExportingReader



class ReadWiserApp(ExportingReader):
    """
    ReadWiser implementation
    This syncs notes and highlights from ReadWiser, which gets them from Moon Reader Pro+
    """

    # app_name should be the same as the class name
    app_name = 'ReadWiser'
    import_fingerprint = True
    initial_dialog_text = "Annotations will be retrieved from the cloud.\nTo appease the plugin, please edit this text to import data for just this book, or replace it with 'all' to import data for all books."
    import_dialog_title = "Import annotations from {0}".format(app_name)

    import_help_text = ('''
            <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
            <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
            <title>Exporting from SampleExportingApp</title>
            <style type="text/css">
                body {
                font-family:Tahoma, Geneva, sans-serif;
                font-size:medium;
                }
                div.steps_with_header h3 {
                    margin:0;
                }
                div.steps_with_header ol, ul {
                    margin-top:0;
                }
                div.steps_with_header_indent p {
                    margin:0 0 0 1em;
                }
                div.steps_with_header_indent ol, ul {
                    margin-left:1em;
                    margin-top:0;
                }
                h2, h3 {
                    font-family:Tahoma, Geneva, sans-serif;
                    text-align: left;
                    font-weight: normal;
                }
            </style>
            </head>
            <body>
                <h3>Exporting annotations from SampleExportingApp</h3>
                <div class="steps_with_header_indent">
                  <p><i>From within an open book:</i></p>
                  <ol>
                    <li>Tap the center of the screen so that the overlay controls are shown</li>
                    <li>Tap <b>Bookmarks</b> (page marker icon at bottom center)</li>
                    <li>Tap <b>Bookmarks</b> (top right)</li>
                    <li>Tap <b>Share</b> (arrow icon at top right)</li>
                    <li>Tap <b>Email</b>, then email the annotations file to yourself</li>
                  </ol>
                </div>
                <hr width="80%" />
                <h3>Importing SampleExportingApp annotations to calibre</h3>
                <div class="steps_with_header_indent">
                  <p><i>After receiving the emailed annotations summary on your computer:</i></p>
                  <ol>
                    <li>Copy the contents of the annotations summary email</li>
                    <li>Paste the annotations to the <b>Import SampleExportingApp annotations</b> window</li>
                    <li>Click <b>Import</b></li>
                  </ol>
                </div>
            </body>
            </html>''')
    
    debug = True
    api_base_url_local = "https://localhost:7113" # TODO: Get this from plugin config?
    api_base_url_prod = "https://readwiser-api.azurewebsites.net" # TODO: Get this from plugin config?
    api_key = "hYN2J49m5yb37fvn6V7EL7C9D8piVEgTaKhThWdqt7CLHg9L" # TODO: Get this from plugin config?
    
    # Change this to True when developing a new class from this template
    SUPPORTS_EXPORTING = True

    def parse_exported_highlights(self, raw, log_failure=True):
        
        self._log("{:~^80}".format(" Starting ReadWiser Import "))
        
        # Create the annotations, books table as needed
        self.annotations_db = "%s_imported_annotations" % self.app_name_
        self.create_annotations_table(self.annotations_db)
        self.books_db = "%s_imported_books" % self.app_name_
        self.create_books_table(self.books_db)

        self.annotated_book_list = []
        self.selected_books = None

        # Generate the book metadata from the selected book
        row = self.opts.gui.library_view.currentIndex() # User selected row
        book_id = self.opts.gui.library_view.model().id(row)
        db = self.opts.gui.current_db
        mi = db.get_metadata(book_id, index_is_id=True) # Get book user has selected

        # Populate author, title at a minimum

        # Call API for one book
        if raw == "all":
            data = self.call_api_for_all()
        else:
            data = self.call_api_for_one_book(mi.authors[0], mi.title)

        for book in data['books']:
            # Populate a BookStruct
            book_struct = BookStruct()

            if raw != "all":
                book_struct.book_id = mi.id            
                book_struct.cid = mi.id
            else:
                book_struct.book_id = book['id']
                book_struct.cid = book['id']

            book_struct.active = True
            book_struct.author = book['author']
            book_struct.title = book['title']
            book_struct.uuid = None
            book_struct.last_update = time.mktime(time.localtime())
            book_struct.reader_app = self.app_name
            book_struct.annotations = len(book['highlights'])

            # Add annotations to the database
            for highlight in book['highlights']:
                # Populate an AnnotationStruct
                annotation = AnnotationStruct()

                # Calculate timestamp from date
                updatedDateTime = datetime.datetime.strptime(highlight['timestamp'], "%Y-%m-%dT%H:%M:%S")
                timestamp = time.mktime(updatedDateTime.timetuple()) # convert datetime object to float
                book_struct.last_update = timestamp

                # Required items
                annotation.book_id = book_struct['book_id']
                annotation.last_modification = timestamp
                # annotation.reader = self.app_name
                annotation.highlight_color = "Green" # default color

                # Optional items
                if 'id' in highlight:
                    annotation.annotation_id = highlight['id']

                if 'highlightColor' in highlight:
                    annotation.highlight_color = highlight['highlightColor']

                if 'highlightText' in highlight:
                    annotation.highlight_text = highlight['highlightText']

                if 'noteText' in highlight:
                    annotation.note_text = highlight['noteText']
                
                if 'location' in highlight:
                    annotation.location = highlight['location']
                
                if 'locationSort' in highlight:
                    annotation.location_sort = highlight['locationSort']

                # Add annotation to annotations_db
                self.add_to_annotations_db(self.annotations_db, annotation)

                # Increment the progress bar
                self.opts.pb.increment()

                # Update last_annotation in books_db
                self.update_book_last_annotation(self.books_db, timestamp, annotation.book_id)

            # Add book to books_db
            self.add_to_books_db(self.books_db, book_struct)
            self.annotated_book_list.append(book_struct)

        # Update the timestamp
        self.update_timestamp(self.annotations_db)
        self.update_timestamp(self.books_db)
        self.commit()

        # Return True if successful
        return True

    
    def call_api_for_one_book(self, author, title):
        self._log("Calling ReaderWiser API for [{0} - {1}]".format(author, title))
        
        if self.debug:
            url = self.api_base_url_local
        else:
            url = self.api_base_url_prod

        url = "{0}/api/highlight/book?title={1}&author={2}".format(url, urllib.parse.quote(title), urllib.parse.quote(author))
        
        return self.call_api(url)



    def call_api_for_all(self):
        self._log("Calling ReaderWiser API for all highlights")
        
        if self.debug:
            url = self.api_base_url_local
        else:
            url = self.api_base_url_prod

        url = "{0}/api/highlight".format(url)

        return self.call_api(url)



    def call_api(self, url):
        self._log("URL: {0}".format(url))

        req = Request(url)
        req.add_header('Authorization', 'Token {0}'.format(self.api_key))
        req.add_header("Accept", "application/json")

        if self.debug:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE    

            response = urlopen(req, context=ctx)
        else:
            response = urlopen(req)

        self._log(response.status)

        body = response.read().decode(response.headers.get_content_charset())
        self._log("{:~^80}".format(" Response Body "))
        self._log(body)
        self._log("{:~^80}".format(" End of Response Body "))

        data = json.loads(body)

        return data