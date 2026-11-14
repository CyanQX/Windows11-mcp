"""Same-user/session manager mutex, held from before Qt imports until UI exit."""
from __future__ import annotations

import hashlib
from pathlib import Path

from installer.models import ManagerError


class ManagerInstance:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.handle = None

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
        self.handle = win32event.CreateMutex(None, True, rf"Local\Actilo.Manager.{sid}.{session}.{scope}")
        if win32api.GetLastError() == 183:
            self.handle.Close()
            self.handle = None
            raise ManagerError("INSTALLER_BUSY", "Actilo 已在运行，正在激活窗口")
        return self

    def __exit__(self, *_):
        import win32event
        if self.handle is not None:
            win32event.ReleaseMutex(self.handle)
            self.handle.Close()
            self.handle = None
