"""Notify a human review UI; this module never approves a request."""

from __future__ import annotations

import os
import subprocess
import sys

PROPERTY = "WindowsMCP.HumanOperatorReview"


def mark_operator_window(widget):
    """OS-owned HWND lifetime: no editable marker file can unlock the review UI."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    hwnd = int(widget.winId())
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    user.SetPropW.restype = wintypes.BOOL
    if not user.SetPropW(hwnd, PROPERTY, 1):
        raise ctypes.WinError(ctypes.get_last_error())


def operator_review_active():
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = wintypes.HANDLE
    found = False
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        nonlocal found
        if user.GetPropW(hwnd, PROPERTY):
            found = True
            return False
        return True
    callback = callback_type(visit)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(callback, 0)
    return found


def notify_operator(request_id: str) -> bool:
    if len(request_id) != 32 or any(ch not in "0123456789abcdef" for ch in request_id):
        return False
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        subprocess.Popen([sys.executable, "--review-request", request_id], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except OSError:
        return False
