# coding: utf-8

from __future__ import annotations
import subprocess
from pathlib import Path
from bookworm import typehints as t
from bookworm import app
from bookworm.paths import app_path
from bookworm.logger import logger


log = logger.getChild(__name__)


def _get_pandoc_executable_path():
    if app.is_frozen:
        return app_path("pandoc", "pandoc.exe")
    else:
        return  Path.cwd().joinpath(".pandoc", "pandoc.exe")


def pandoc_convert(input_data, from_format, to_format):
        args = [
            _get_pandoc_executable_path(),
            "--from",
            from_format,
            "--to",
            to_format
        ]
        creationflags = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        ret = subprocess.run(
            args,
            input=input_data,
            capture_output=True,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        return ret.stdout.decode("utf-8")
