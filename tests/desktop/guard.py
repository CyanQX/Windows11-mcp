"""Hard safety net for desktop tests: never inject input outside the fixture process.

Every helper that sends real input first calls :func:`assert_point_owned` and/or
:func:`assert_foreground_owned`. They re-check ownership immediately before the input is sent,
so a crashed fixture, a window that popped up on top, or the user clicking elsewhere aborts the
test instead of clicking/typing into somebody else's window.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.WindowFromPoint.argtypes = (wt.POINT,)
_user32.WindowFromPoint.restype = wt.HWND
_user32.GetForegroundWindow.restype = wt.HWND
_user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
_user32.GetAncestor.argtypes = (wt.HWND, wt.UINT)
_user32.GetAncestor.restype = wt.HWND


class ForeignInputTarget(AssertionError):
    """Raised instead of sending input that could reach a non-fixture window."""


def pid_of(hwnd: int | None) -> int:
    if not hwnd:
        return 0
    pid = wt.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def assert_point_owned(x: int, y: int, pids: set[int]) -> None:
    hwnd = _user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    owner = pid_of(hwnd)
    if owner not in pids:
        raise ForeignInputTarget(
            f"refusing to inject input at ({x},{y}): window 0x{(hwnd or 0):x} belongs to pid {owner}, "
            f"not to the fixture pids {sorted(pids)}"
        )


def assert_foreground_owned(pids: set[int]) -> None:
    hwnd = _user32.GetForegroundWindow()
    owner = pid_of(hwnd)
    if owner not in pids:
        raise ForeignInputTarget(
            f"refusing to send keystrokes: the foreground window 0x{(hwnd or 0):x} belongs to pid {owner}, "
            f"not to the fixture pids {sorted(pids)}"
        )
