# coding: utf-8

from __future__ import annotations
import dateparser
import lxml
from functools import cached_property
from pathlib import Path
from more_itertools import flatten, first as get_first_element
from bookworm import app
from bookworm.i18n import LocaleInfo
from bookworm.pandoc import pandoc_convert
from bookworm.logger import logger
from .. import (
    BookMetadata,
    DocumentError,
    DocumentIOError,
)
from .html import BaseHtmlDocument


log = logger.getChild(__name__)


class DocbookDocument(BaseHtmlDocument):
    """Docbook is a format for writing technical documentation. It uses it's own markup."""

    format = "docbook"
    # Translators: the name of a document file format
    name = _("Docbook Document")
    extensions = ("*.dbk", "*.docbook",)

    def read(self):
        self.filename = self.get_file_system_path()
        with open(self.filename, "rb") as file:
            self._raw_docbook_xml = file.read()
            self.xml_tree = lxml.etree.fromstring(self._raw_docbook_xml)
        super().read()

    @cached_property
    def language(self):
        if lang_tag := self.xml_tree.attrib.get("lang"):
            try:
                return LocaleInfo(lang_tag)
            except ValueError:
                pass
        plane_text = "\n".join(self.xml_tree.itertext(tag="para"))
        return self.get_language(plane_text, is_html=False)

    @cached_property
    def metadata(self):
        return self._get_book_metadata()

    def _get_book_metadata(self):
        xml_tree = self.xml_tree
        if not (title := xml_tree.xpath("/book/title//text()")):
            title = xml_tree.xpath("/book/bookinfo/title//text()")
        title = get_first_element(title, Path(self.get_file_system_path()).stem)
        author_firstname = get_first_element(
            xml_tree.xpath("/book/bookinfo/author/firstname//text()"), ""
        )
        author_surname = get_first_element(
            xml_tree.xpath("/book/bookinfo/author/surname//text()"), ""
        )
        author = " ".join(
            [
                author_firstname,
                author_surname,
            ]
        )
        publisher = get_first_element(
            xml_tree.xpath("/book/bookinfo/corpname//text()"), ""
        )
        creation_date = xml_tree.xpath("/book/bookinfo/date//text()")
        if creation_date:
            parsed_date = dateparser.parse(
                creation_date[0],
                languages=[
                    self.language.two_letter_language_code,
                ],
            )
            creation_date = self.language.format_datetime(
                parsed_date, date_only=True, format="long", localized=True
            )
        return BookMetadata(
            title=title,
            author=author,
            creation_date=creation_date,
            publisher=publisher,
        )

    def get_html(self):
        if (html_string := getattr(self, "html_string", None)) is not None:
            return html_string
        return self._get_html_from_docbook()

    def parse_html(self):
        return self.parse_to_full_text()

    def _get_html_from_docbook(self):
        return pandoc_convert(
            self._raw_docbook_xml,
            from_format='docbook',
            to_format='html'
        )
