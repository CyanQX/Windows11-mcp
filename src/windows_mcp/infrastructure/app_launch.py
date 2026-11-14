"""Resolve desktop shortcuts without running ShellExecute elevation verbs."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct

from windows_mcp.infrastructure.execution_security import ExecutionDenied


def shortcut_command(path):
    with Path(path).open("rb") as stream:
        header = stream.read(24)
    if len(header) >= 24 and struct.unpack_from("<I", header, 20)[0] & 0x2000:
        raise ExecutionDenied("ELEVATION_REQUEST_DENIED: shortcut requests RunAsUser")
    import pythoncom
    from win32com.shell import shell
    pythoncom.CoInitialize()
    try:
        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path), 0)
        executable = os.path.expandvars(link.GetPath(0)[0])
        arguments, directory = link.GetArguments(), link.GetWorkingDirectory()
    finally:
        pythoncom.CoUninitialize()
    shell32, kernel32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    values = shell32.CommandLineToArgvW('placeholder ' + arguments, ctypes.byref(count))
    if not values:
        raise OSError("Cannot parse shortcut arguments")
    try:
        args = [values[index] for index in range(1, count.value)]
    finally:
        kernel32.LocalFree(values)
    return [executable, *args], os.path.expandvars(directory) or None


def find_start_shortcut(name):
    roots = [Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs"]
    found, scanned = [], 0
    for root in roots:
        for directory, folders, files in os.walk(root, followlinks=False):
            folders[:] = [item for item in folders if not (Path(directory) / item).is_junction() and not (Path(directory) / item).is_symlink()]
            for filename in files:
                scanned += 1
                if scanned > 6000:
                    raise ValueError("Start Menu lookup exceeded its bound; use an exact executable path")
                path = Path(directory) / filename
                if path.suffix.casefold() == ".lnk" and path.stem.casefold() == name.casefold() and not path.is_symlink():
                    found.append(path)
    if len(found) > 1:
        raise ValueError("Multiple Start Menu shortcuts match; use an exact executable path")
    return found[0] if found else None
