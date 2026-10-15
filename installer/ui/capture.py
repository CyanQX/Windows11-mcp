"""Capture only our own HWND for deterministic native GUI review."""

import ctypes
from ctypes import wintypes
from pathlib import Path

from PIL import Image


def capture(window, target: Path):
    import win32gui
    import win32ui
    window.update_idletasks()
    user = ctypes.WinDLL("user32")
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    handle = user.GetParent(window.winfo_id()) or window.winfo_id()
    outer = wintypes.RECT()
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not user.GetWindowRect(handle, ctypes.byref(outer)):
        raise ctypes.WinError()
    window_dc = win32gui.GetWindowDC(handle)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        size = outer.right - outer.left, outer.bottom - outer.top
        bitmap.CreateCompatibleBitmap(source_dc, *size)
        memory_dc.SelectObject(bitmap)
        user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        if not user.PrintWindow(handle, memory_dc.GetSafeHdc(), 2):
            raise RuntimeError("Cannot capture our native window")
        image = Image.frombytes("RGB", size, bitmap.GetBitmapBits(True), "raw", "BGRX")
    finally:
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(handle, window_dc)
        win32gui.DeleteObject(bitmap.GetHandle())
    target.parent.mkdir(parents=True, exist_ok=True)
    client = window.winfo_rootx() - outer.left, window.winfo_rooty() - outer.top
    image.crop((*client, client[0] + window.winfo_width(), client[1] + window.winfo_height())).save(target)
    dwm = ctypes.WinDLL("dwmapi")
    bounds = wintypes.RECT()
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    if dwm.DwmGetWindowAttribute(handle, 9, ctypes.byref(bounds), ctypes.sizeof(bounds)) == 0:
        image.crop((bounds.left - outer.left, bounds.top - outer.top, bounds.right - outer.left, bounds.bottom - outer.top)).save(target.with_name(target.stem + "-window.png"))
