"""Notify a human review UI; this module never approves a request."""

from __future__ import annotations

import os
import subprocess
import sys

PROPERTY = "WindowsMCP.HumanOperatorReview"
PAUSE_PROPERTY = PROPERTY + ".PendingDecision"


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
    set_operator_pause(widget, True)


def set_operator_pause(widget, paused: bool):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    if not user.SetPropW(int(widget.winId()), PAUSE_PROPERTY, 1 if paused else 2):
        raise ctypes.WinError(ctypes.get_last_error())


def is_operator_window(hwnd: int) -> bool:
    """Protect the settings surface and its owned modal/popup windows independently
    of whether ordinary desktop work is currently paused.
    """
    if os.name != "nt" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = wintypes.HANDLE
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    seen = set()
    while hwnd and hwnd not in seen and len(seen) < 32:
        seen.add(hwnd)
        if user.GetPropW(hwnd, PROPERTY):
            return True
        root = user.GetAncestor(hwnd, 2)  # GA_ROOT includes native child controls.
        if root and user.GetPropW(root, PROPERTY):
            return True
        hwnd = user.GetWindow(root or hwnd, 4)  # GW_OWNER includes modal sheets.
    return False


def operator_review_active():
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = wintypes.HANDLE
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    found = False
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        nonlocal found
        if (user.IsWindowVisible(hwnd) and user.GetPropW(hwnd, PROPERTY)
                and user.GetPropW(hwnd, PAUSE_PROPERTY) != 2):
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
    from windows_mcp.infrastructure.config import CONFIG_DIR
    from windows_mcp.infrastructure.trust import TrustStore

    if TrustStore(CONFIG_DIR / "approvals").automatic():
        return False
    from windows_mcp.infrastructure.process_env import independent_environment
    try:
        subprocess.Popen([sys.executable, "--review-request", request_id], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW, env=independent_environment())
        return True
    except OSError:
        return False
