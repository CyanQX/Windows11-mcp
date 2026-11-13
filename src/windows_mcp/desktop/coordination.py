"""Cooperating Windows-MCP processes share a desktop job lease in this logon session."""

from __future__ import annotations

import threading
from contextlib import contextmanager

import win32api
import win32event

NAME = r"Local\WindowsMCP.Desktop.v1"
_local = threading.local()


class DesktopBusyError(RuntimeError):
    pass


@contextmanager
def desktop_lease():
    """Fail before running a queued job; never replay it after another process's action.

    A timed-out UIA job retains this lease until its worker actually returns. Windows
    releases an abandoned mutex if its owning thread/process exits. Other automation
    products do not participate; this is coordination, not a security boundary.
    """
    if getattr(_local, "held", False):
        yield
        return
    handle = win32event.CreateMutex(None, False, NAME)
    acquired = False
    try:
        result = win32event.WaitForSingleObject(handle, 0)
        if result not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            raise DesktopBusyError("DESKTOP_BUSY: another Windows-MCP process owns the desktop; this job was not started")
        acquired = _local.held = True
        yield
    finally:
        if acquired:
            _local.held = False
            win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)


def status() -> dict:
    if getattr(_local, "held", False):
        return {"scope": "windows_logon_session", "busy": True, "owned_by_this_thread": True}
    handle = win32event.CreateMutex(None, False, NAME)
    try:
        result = win32event.WaitForSingleObject(handle, 0)
        acquired = result in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        if acquired:
            win32event.ReleaseMutex(handle)
        return {"scope": "windows_logon_session", "busy": not acquired, "owned_by_this_thread": False}
    finally:
        win32api.CloseHandle(handle)
