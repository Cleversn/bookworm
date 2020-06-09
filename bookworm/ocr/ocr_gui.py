# coding: utf-8

import gc
import os
import threading
import time
import operator
import wx
import multiprocessing as mp
from pathlib import Path
from lru import LRU
from enum import IntEnum
from bookworm import app
from bookworm import config
from bookworm import speech
from bookworm.signals import (
    reader_book_unloaded,
    reader_page_changed,
    ocr_started,
    ocr_ended,
    speech_engine_state_changed
)
from bookworm.concurrency import call_threaded, process_worker
from bookworm.resources import sounds
from bookworm.speechdriver.enumerations import SynthState
from bookworm.gui.components import SimpleDialog, SnakDialog
from bookworm.utils import gui_thread_safe
from bookworm.logger import logger
from . import ocr_provider as ocr


log = logger.getChild(__name__)


class OCRMenuIds(IntEnum):
    scanCurrentPage = 10001
    autoScanPages = 10002
    scanToTextFile = 10003
    changeOCROptions = 10004


OCR_KEYBOARD_SHORTCUTS = {
    OCRMenuIds.scanCurrentPage: "F4",
    OCRMenuIds.autoScanPages: "Ctrl-F4",
}


class OCROptionsDialog(SimpleDialog):
    """OCR options."""

    def __init__(self, *args, saved_values=None, choices=(), force_save=False, **kwargs):
        self.saved_values = saved_values
        self.choices = choices
        self.force_save = force_save
        self._return_value = None
        super().__init__(*args, **kwargs)

    def addControls(self, parent):
        # Translators: the label of a combobox
        label = wx.StaticText(parent, -1, _("Recognition Language:"))
        self.langChoice = wx.Choice(parent, -1, choices=self.choices)
        self.langChoice.SetSizerProps(expand=True)
        wx.StaticText(parent, -1, _("Supplied Image resolution::"))
        self.zoomFactorSlider = wx.Slider(parent, -1, minValue=1, maxValue=20)
        self.enhanceImageCheckbox = wx.CheckBox(
            parent,
            -1,
            # Translators: the label of a checkbox
            _("Enhance image before recognition"),
        )
        wx.StaticLine(parent)
        if not self.force_save:
            self.saveOptionsCheckbox = wx.CheckBox(
                parent,
                -1,
                # Translators: the label of a checkbox
                _("&Save these options until I close the current book"),
            )
        self.Bind(wx.EVT_BUTTON, self.onOK, id=wx.ID_OK)
        if not self.saved_values:
            self.langChoice.SetSelection(0)
            self.zoomFactorSlider.SetValue(4)
            self.enhanceImageCheckbox.SetValue(True)
        else:
            self.langChoice.SetSelection(self.saved_values["lang"])
            self.zoomFactorSlider.SetValue(self.saved_values["zoom_factor"])
            self.enhanceImageCheckbox.SetValue(self.saved_values["should_enhance"])
            if not self.force_save:
                self.saveOptionsCheckbox.SetValue(self.saved_values["save_options"])

    def onOK(self, event):
        self._return_value = (
            self.langChoice.GetSelection(),
            self.zoomFactorSlider.GetValue(),
            self.enhanceImageCheckbox.IsChecked(),
            self.force_save or self.saveOptionsCheckbox.IsChecked()
        )
        self.Close()

    def ShowModal(self):
        super().ShowModal()
        return self._return_value


class OCRMenu(wx.Menu):
    """OCR menu."""

    def __init__(self, service, menubar):
        super().__init__()
        self.service = service
        self.menubar = menubar
        self.view = service.view
        self._ocr_wait_dlg = SnakDialog(parent=self.view, message=_("Scanning page. Please wait...."), dismiss_callback=self._on_ocr_cancelled)
        self._ocr_cancelled = threading.Event()
        self._saved_ocr_options = []
        self._scanned_pages = LRU(size=100)
        image2textId = wx.NewIdRef()

        # Add menu items
        self.Append(
            OCRMenuIds.scanCurrentPage,
            # Translators: the label of an item in the application menubar
            _("S&can Current Page...\tF4"),
            # Translators: the help text of an item in the application menubar
            _("OCR current page")
        )
        self.auto_scan_item = self.Append(
            OCRMenuIds.autoScanPages,
            # Translators: the label of an item in the application menubar
            _("&Realtime OCR\tCtrl-F4"),
            # Translators: the help text of an item in the application menubar
            _("Auto run  OCR when turning pages."),
            kind=wx.ITEM_CHECK
        )
        self.Append(
            OCRMenuIds.changeOCROptions,
            # Translators: the label of an item in the application menubar
            _("&Change OCR Options..."),
            # Translators: the help text of an item in the application menubar
            _("Change OCR options"),
        )
        self.Append(
            OCRMenuIds.scanToTextFile,
            # Translators: the label of an item in the application menubar
            _("Scan To Text File..."),
            # Translators: the help text of an item in the application menubar
            _("Scan pages and save the text to a .txt file."),
        )
        self.Append(
            image2textId,
            # Translators: the label of an item in the application menubar
            _("Image To Text..."),
            # Translators: the help text of an item in the application menubar
            _("Run OCR on an image."),
        )
        # Add the menu to the menubar
        # Translators: the label of the OCR menu in the application menubar
        self.view.toolsMenu.Append(wx.ID_ANY, _("OCR Tools"), self)
        
        # Event handlers
        self.view.Bind(
            wx.EVT_MENU,
            self.onScanCurrentPage,
            id=OCRMenuIds.scanCurrentPage,
        )
        self.view.Bind(
            wx.EVT_MENU,
            self.onAutoScanPages,
            id=OCRMenuIds.autoScanPages,
        )
        self.view.Bind(
            wx.EVT_MENU,
            self.onScanToTextFile,
            id=OCRMenuIds.scanToTextFile,
        )
        self.view.Bind(
            wx.EVT_MENU,
            self.onChangeOCROptions,
            id=OCRMenuIds.changeOCROptions,
        )
        self.view.Bind(
            wx.EVT_MENU,
            self.onScanImageFile,
            id=image2textId,
        )
        reader_book_unloaded.connect(self._on_reader_unloaded, sender=self.view.reader)
        reader_page_changed.connect(self._on_reader_page_changed, sender=self.service.reader)
        speech_engine_state_changed.connect(self._on_speech_engine_state_change, sender=self.view)

    def _get_ocr_options(self, from_cache=True, **dlg_kw):
        pre_saved = list(self._saved_ocr_options)
        if not from_cache:
            self._saved_ocr_options.clear()
            self._scanned_pages.clear()
        if self._saved_ocr_options:
            rv = self._saved_ocr_options
        else:
            saved_values = {}
            if pre_saved:
                saved_values["lang"] = [l.given_lang for l in ocr.get_recognition_languages()].index(pre_saved[0])
                saved_values["zoom_factor"] = pre_saved[1]
                saved_values["should_enhance"] = pre_saved[2]
                saved_values["save_options"] = True
            opts = self._get_ocr_options_from_dlg(saved_values=saved_values, **dlg_kw)
            if opts is None:
                return
            *rv, should_save = opts
            if should_save:
                self._saved_ocr_options = rv
        return rv

    def _get_ocr_options_from_dlg(self, saved_values=None, **dlg_kw):
        langs = ocr.get_recognition_languages()
        if not langs:
            wx.MessageBox(
                # Translators: content of a message
                _("No language for OCR is present.\bPlease use Windos Regional Settings to download some languages."),
                _("No OCR Languages"),
                style=wx.ICON_ERROR
            )
            return
        dlg = OCROptionsDialog(
            parent=self.view,
            title=_("OCR Options"),
            choices=[l.description for l in langs],
            saved_values=saved_values or {},
            **dlg_kw
        )
        ocr_opts = dlg.ShowModal()
        if ocr_opts is not None:
            res = list(ocr_opts)
            res[0] = langs[res[0]].given_lang
            return res

    def onScanCurrentPage(self, event):
        self._ocr_cancelled.clear()
        res = self._get_ocr_options()
        if res is None:
            return speech.announce(_("Cancelled"), True)
        lang, zoom_factor, should_enhance = res
        reader =self.service.reader
        if reader.current_page in self._scanned_pages:
            self.view.set_content(self._scanned_pages[reader.current_page])
            return
        image, width, height = reader.document.get_page_image(reader.current_page, zoom_factor, should_enhance)
        recog = ocr.ImageRecognizer(
            lang=lang,
            imagedata=image,
            width=width,
            height=height,
            cookie=reader.current_page
        )
        ocr_started.send(sender=self.view)
        # Show a modal dialog
        self._ocr_wait_dlg.Show()
        sounds.ocr_start.play()
        process_worker.submit(recog.recognize).add_done_callback(self._process_ocr_result)

    def onAutoScanPages(self, event):
        event.Skip()
        if not self._saved_ocr_options:
            self._get_ocr_options(force_save=True)
        if self.auto_scan_item.IsChecked():
            speech.announce(_("Realtime OCR is enabled"))
        else:
            speech.announce(_("Realtime OCR is disabled"))
        if not self.view.contentTextCtrl.GetValue():
            pass
            #self.onScanCurrentPage(event)

    def onScanToTextFile(self, event):
        res = self._get_ocr_options(from_cache=False, force_save=True)
        if res is None:
            return
        # Get output file path
        filename = f"{self.view.reader.current_book.title}.txt"
        saveExportedFD = wx.FileDialog(
            self.view,
            # Translators: the title of a save file dialog asking the user for a filename to export notes to
            _("Save as"),
            defaultDir=wx.GetUserHome(),
            defaultFile=filename,
            # Translators: file type in a save as dialog
            wildcard=_("Plain Text (*.txt)|.txt"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if saveExportedFD.ShowModal() != wx.ID_OK:
            return
        output_file = saveExportedFD.GetPath().strip()
        saveExportedFD.Destroy()
        if not output_file:
            return
        # Continue with OCR
        progress_dlg = wx.ProgressDialog(
            # Translators: the title of a progress dialog
            _("Scanning Pages"),
            # Translators: the message of a progress dialog
            _("Preparing book"),
            parent=self.view,
            maximum=len(self.service.reader.document),
            style=wx.PD_APP_MODAL | wx.PD_REMAINING_TIME | wx.PD_AUTO_HIDE,
        )
        self._continue_with_text_extraction(res, output_file, progress_dlg)

    @call_threaded
    def _continue_with_text_extraction(self, options, output_file, progress_dlg):
        lang, zoom_factor, should_enhance = options
        doc = self.service.reader.document
        total = len(doc)
        ocr_executor = ocr.scan_to_text(
            doc.__class__,
            doc.filename,
            lang, zoom_factor, should_enhance,
            output_file
        )
        for progress in ocr_executor:
            wx.CallAfter(progress_dlg.Update, progress, f"Scanning page {progress} of {total}")
        wx.CallAfter(dlg.Close)
        wx.CallAfter(dlg.Destroy)

    def onChangeOCROptions(self, event):
        self._get_ocr_options(from_cache=False)

    def onScanImageFile(self, event):
        from fitz import Pixmap

        wildcard = []
        all_exts = [
            ("*.png", _("Portable Network Graphics")),
            ("*.jpg", _("JPEG images")),
            ("*.bmp", _("Bitmap images")),
        ]
        for ext, name in all_exts:
            wildcard.append("{name} ({ext})|{ext}|".format(name=name, ext=ext))
        wildcard[-1] = wildcard[-1].rstrip("|")
        allfiles = ";".join(ext[0] for ext in all_exts)
        wildcard.insert(
            0,
            _("All supported image formats|{ext}|").format(ext=allfiles),
        )
        openFileDlg = wx.FileDialog(
            self.view,
            # Translators: the title of a file dialog to browse to an image
            message=_("Choose image file"),
            defaultDir=str(Path.home()),
            wildcard="".join(wildcard),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if openFileDlg.ShowModal() == wx.ID_OK:
            filename = openFileDlg.GetPath().strip()
            openFileDlg.Destroy()
            if not filename:
                return
            image = Pixmap(filename)
            # Force include the alpha channel
            image = Pixmap(image, True)
            options = self._get_ocr_options_from_dlg(force_save=True)
            if not options:
                return
            lang, *rest = options
            wait_dlg = SnakDialog(parent=self.view, message=_("Running OCR on image. Please wait...."))
    
            @gui_thread_safe
            def ocr_done_callback(future):
                __, content = future.result()
                wait_dlg.Destroy()
                # Translators: label for a text control
                self.view.SetStatusText(_("Recognition Results"))
                self.view.set_content(content)

            wait_dlg.Show()
            recog = ocr.ImageRecognizer(
                lang=lang,
                imagedata=image.samples,
                width=image.w,
                height=image.h
            )
            process_worker.submit(recog.recognize).add_done_callback(ocr_done_callback)

    @gui_thread_safe
    def _process_ocr_result(self, task):
        if self._ocr_cancelled.is_set():
            ocr_ended.send(sender=self.view, isfaulted=True)
            return
        try:
            page_number, content = task.result()
        except Exception as e:
            self._ocr_wait_dlg.Hide()
            log.exception(f"Error getting OCR recognition results: {e}")
            ocr_ended.send(sender=self.view, isfaulted=True)
            return
        ocr_ended.send(sender=self.view, isfaulted=False)
        self._scanned_pages[page_number] = content
        if page_number == self.view.reader.current_page:
            self.view.set_content(content)
            sounds.ocr_end.play()
            speech.announce(_("Scan finished."), urgent=True)
            self._ocr_wait_dlg.Hide()
            self.view.contentTextCtrl.SetFocusFromKbd()

    def _on_ocr_cancelled(self):
        self._ocr_cancelled.set()
        speech.announce(_("OCR cancelled"), True)
        return True

    def _on_reader_unloaded(self, sender):
        self._scanned_pages.clear()
        self._saved_ocr_options.clear()
        self.auto_scan_item.Check(False)

    def _on_reader_page_changed(self, sender, current, prev):
        if self.auto_scan_item.IsChecked():
            self.onScanCurrentPage(None)

    def _on_speech_engine_state_change(self, sender, service, state):
        ...