# coding: utf-8

from __future__ import annotations
import subprocess
from pathlib import Path
from bookworm import typehints as t
from bookworm import app
from bookworm.paths import app_path
from bookworm.logger import logger


log = logger.getChild(__name__)
PANDOC_HEAP_SIZE_IN_MB = 500

    
def _get_pandoc_executable_path():
    if app.is_frozen:
        return app_path("pandoc", "pandoc.exe")
    else:
        return  Path.cwd().joinpath(".pandoc", "pandoc.exe")


def pandoc_convert(input_data, from_format, to_format, output_as_text=True):
        args = [
            _get_pandoc_executable_path(),
            "+RTS",
            f"-M{PANDOC_HEAP_SIZE_IN_MB}m",
            "-RTS",
            "--from",
            from_format,
            "--to",
            to_format
        ]
        if not output_as_text:
            args.extend(["-o", "-"])
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
        if ret.returncode != 0:
            raise IOError(f"Failed to convert document using pandoc.\n{ret.stderr.decode('utf-8')}")
        if output_as_text:
            return ret.stdout.decode("utf-8")
        else:
            return ret.stdout
