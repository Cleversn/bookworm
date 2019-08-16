# coding: utf-8

import clr
import System
from Microsoft.Win32 import Registry, RegistryKey, RegistryValueKind

import sys
import os
from typing import Iterable
from dataclasses import dataclass
from bookworm import app
from bookworm.reader import EBookReader
from bookworm.vendor import shellapi
from bookworm.logger import logger


log = logger.getChild(__name__)


@dataclass
class RegKey:
    """Represent a key in the windows registry."""

    root: RegistryKey
    path: str
    writable: bool = True
    ensure_created: bool = False

    def __post_init__(self):
        self.key = self.root.OpenSubKey(self.path, self.writable)
        if self.key is None and self.ensure_created:
            self.create()

    def close(self):
        if self.key is not None:
            self.key.Close()
            self.key.Dispose()

    @property
    def exists(self):
        return self.key is not None

    def create(self):
        self.key = self.root.CreateSubKey(self.path)

    def __getattr__(self, attr):
        return getattr(self.key, attr)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @classmethod
    def LocalSoftware(cls, *args, **kwargs):
        root = cls(Registry.CurrentUser, r"SOFTWARE\Classes", kwargs.pop("writable", True))
        return cls(root, *args, **kwargs)


def add_shell_command(key, executable):
    key.CreateSubKey(r"shell\Read\command").SetValue(
        "",
        f'"{executable}" "%1"',
        RegistryValueKind.String
    )


def register_application(prog_id, executable, supported_exts):
    exe = os.path.split(executable)[-1]
    with RegKey.LocalSoftware(fr"Applications\{exe}", ensure_created=True) as exe_key:
        add_shell_command(exe_key, executable)
        with RegKey(exe_key, "SupportedTypes", ensure_created=True) as supkey:
            for ext in supported_exts:
                supkey.SetValue(ext, "", RegistryValueKind.String)


def associate_extension(ext, prog_id, executable, desc, icon=None):
    # Add the prog_id
    with RegKey.LocalSoftware(prog_id, ensure_created=True) as progkey:
        progkey.SetValue("", desc)
        with RegKey(progkey, "DefaultIcon", ensure_created=True) as iconkey:
            iconkey.SetValue("", icon or executable)
        add_shell_command(progkey, executable)
    # Associate file type
    with RegKey.LocalSoftware(fr"{ext}\OpenWithProgids", ensure_created=True) as askey:
        askey.SetValue(prog_id, System.Array[System.Byte]([]), RegistryValueKind.Binary)
    # Set this executable as the default handler for files with this extension
    with RegKey.LocalSoftware(ext, ensure_created=True) as defkey:
        defkey.SetValue("", prog_id)
    shellapi.SHChangeNotify(shellapi.SHCNE_ASSOCCHANGED, shellapi.SHCNF_IDLIST, None, None)


def remove_association(ext, prog_id):
    try:
        with RegKey.LocalSoftware("") as k:
            k.DeleteSubKeyTree(prog_id)
        with RegKey.LocalSoftware(fr"{ext}\OpenWithProgids") as k:
            k.DeleteSubKey(prog_id)
        shellapi.SHChangeNotify(
            shellapi.SHCNE_ASSOCCHANGED,
            shellapi.SHCNF_IDLIST,
            None,
            None
        )
    except System.ArgumentException:
        log.exception(
            f"Error removing association for extension {ext} with prog_id {prog_id}",
            exc_info=True
        )


def get_ext_info(supported="*"):
    doctypes = {}
    for cls in EBookReader.document_classes:
        for ext in cls.extensions:
            if (supported == "*") or (ext in supported):
                doctypes[ext.replace("*", "")] = (f"{app.prog_id}.{cls.format}", _(cls.name))
    return doctypes


def shell_integrate(supported="*"):
    register_application(app.prog_id, sys.executable, supported)
    doctypes = get_ext_info(supported)
    for (ext, (prog_id, desc)) in doctypes.items():
        associate_extension(ext, prog_id, sys.executable, desc, icon=None)
    return doctypes


def shell_disintegrate(supported="*"):
    doctypes = get_ext_info(supported)
    executable = sys.executable
    for (ext, (prog_id, desc)) in doctypes.items():
        remove_association(ext, prog_id)
    return doctypes
