"""Same-user/session mutex and coalescing activation events, held before Qt."""
from __future__ import annotations

import hashlib
from pathlib import Path

from installer.models import ManagerError


class ManagerInstance:
    def __init__(self, home: Path, *, review=False):
        self.home = home.resolve()
        self.handle = None
        self.events = {}
        self.review = review

    def __enter__(self):
        import win32api
        import win32con
        import win32event
        import win32ts
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        session = win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId())
        scope = hashlib.sha256(str(self.home).casefold().encode()).hexdigest()[:16]
        name = rf"Local\Actilo.Manager.{sid}.{session}.{scope}"
        try:
            # Create events before the mutex: a second click during Qt imports
            # must remain signalled until the first window starts polling. No
            # shared file is replaced or deleted while another process reads it.
            for kind in ("activate", "review"):
                self.events[kind] = win32event.CreateEvent(None, False, False, name + "." + kind)
            self.handle = win32event.CreateMutex(None, True, name)
            if win32api.GetLastError() == 183:
                self.handle.Close()
                self.handle = None
                win32event.SetEvent(self.events["review" if self.review else "activate"])
                raise ManagerError("INSTALLER_BUSY", "Actilo 已在运行，正在激活窗口")
        except BaseException:
            self._close_events()
            raise
        return self

    def consume_activation(self):
        import win32event
        activated = None
        for kind in ("activate", "review"):
            handle = self.events.get(kind)
            if handle is not None and win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_OBJECT_0:
                activated = kind
        return activated

    def _close_events(self):
        for handle in self.events.values():
            handle.Close()
        self.events.clear()

    def __exit__(self, *_):
        import win32event
        try:
            if self.handle is not None:
                try:
                    win32event.ReleaseMutex(self.handle)
                finally:
                    self.handle.Close()
                    self.handle = None
        finally:
            self._close_events()
