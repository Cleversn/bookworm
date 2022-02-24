# coding: utf-8

from __future__ import annotations
import lxml.html
from bookworm.pandoc import pandoc_convert
from bookworm.logger import logger
from .html import BaseHtmlDocument


log = logger.getChild(__name__)


class RtfDocument(BaseHtmlDocument):
    """The good old Rich Text Format."""

    format = "rtf"
    # Translators: the name of a document file format
    name = _("Rich Text Format Document")
    extensions = ("*.rtf",)

    def read(self):
        self.filename = self.get_file_system_path()
        with open(self.filename, "rb") as file:
            self._raw_file_data = file.read()
        super().read()

    def get_html(self):
        if (html_string := getattr(self, "html_string", None)) is not None:
            return html_string
        return self._get_html_from_rtf()

    def parse_html(self):
        return self.parse_to_full_text()

    def _get_html_from_rtf(self):
        raw_html = pandoc_convert(
            self._raw_file_data,
            from_format='rtf',
            to_format='html'
        )
        html_tree = lxml.html.fromstring(raw_html)
        if not (html_tree.xpath("/html/head/title//text()")):
            if not html_tree.xpath('/html/head'):
                head = html_tree.makeelement('head')
                html_tree.insert(0, head)
            if (title_el := html_tree.xpath("/html/head/title")):
                html_tree.remove(title_el)
            title = head.makeelement('title')
            title.text = self.filename.stem
            html_tree.head.insert(0, title)
        return lxml.html.tostring(html_tree, encoding='unicode')


