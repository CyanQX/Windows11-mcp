"""Observe external input and window changes on a dedicated Win32 message thread.

Callbacks only set flags. They never suppress input, call UIA or retain key contents.
The event tag identifies this server's own injected events; it is not a security boundary.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from dataclasses import dataclass, field


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
_EVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


class _Mouse(ctypes.Structure):
    _fields_ = [("point", wt.POINT), ("data", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


class _Keyboard(ctypes.Structure):
    _fields_ = [("key", wt.DWORD), ("scan", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


@dataclass
class GestureToken:
    start_tick: int
    window: int = 0
    reason: str | None = None
    last_own_point: tuple[int, int] | None = None
    interrupted: threading.Event = field(default_factory=threading.Event)
    dirty: threading.Event = field(default_factory=threading.Event)

    def input(self, extra: int, timestamp: int, *, keyboard: bool = False) -> None:
        from windows_mcp.desktop.native_input import EXTRA_INFO

        if extra != EXTRA_INFO and ((timestamp - self.start_tick) & 0xFFFFFFFF) < 0x80000000:
            self.reason = "external_keyboard_input" if keyboard else "external_mouse_input"
            self.interrupted.set()


class PointerMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = None
        self._thread_id = 0
        self._active: GestureToken | None = None
        self._error = None
        self.window_events = False

    def start(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._error = None
                self._ready.clear()
                self._thread = threading.Thread(target=self._run, name="wmcp-pointer-monitor", daemon=True)
                self._thread.start()
        if not self._ready.wait(2.0) or self._error or not self._thread.is_alive():
            raise RuntimeError("POINTER_MONITOR_UNAVAILABLE: external-input monitoring could not start")

    def arm(self, window: int = 0) -> GestureToken:
        self.start()
        from windows_mcp.desktop.native_input import _user32

        if any(_user32.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4)):
            raise RuntimeError("USER_INTERRUPTED: a mouse button is already held; finish manual input first")
        token = GestureToken(ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF, window)
        with self._lock:
            if self._active is not None:
                raise RuntimeError("POINTER_BUSY: another pointer gesture is active")
            self._active = token
        return token

    def disarm(self, token):
        with self._lock:
            if self._active is token:
                self._active = None

    def healthy(self):
        return self._thread is not None and self._thread.is_alive() and self._error is None

    def stop(self):
        if self._thread and self._thread.is_alive():
            user32 = ctypes.windll.user32
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            self._thread.join(2.0)

    def _run(self):
        user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.windll.kernel32
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wt.HINSTANCE, wt.DWORD)
        user32.SetWindowsHookExW.restype = wt.HANDLE
        user32.CallNextHookEx.argtypes = (wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = (wt.HANDLE,)
        user32.SetWinEventHook.argtypes = (wt.DWORD, wt.DWORD, wt.HMODULE, _EVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD)
        user32.SetWinEventHook.restype = wt.HANDLE
        user32.UnhookWinEvent.argtypes = (wt.HANDLE,)
        user32.IsChild.argtypes = (wt.HWND, wt.HWND)
        user32.IsChild.restype = wt.BOOL
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetModuleHandleW.argtypes = (wt.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        hooks, events = [], []

        def mouse(code, message, address):
            token = self._active
            if code >= 0 and token is not None:
                data = ctypes.cast(address, ctypes.POINTER(_Mouse)).contents
                from windows_mcp.desktop.native_input import EXTRA_INFO

                if data.extra == EXTRA_INFO:
                    token.last_own_point = (data.point.x, data.point.y)
                token.input(data.extra, data.time)
            return user32.CallNextHookEx(None, code, message, address)

        def keyboard(code, message, address):
            token = self._active
            if code >= 0 and token is not None:
                data = ctypes.cast(address, ctypes.POINTER(_Keyboard)).contents
                token.input(data.extra, data.time, keyboard=True)
            return user32.CallNextHookEx(None, code, message, address)

        def changed(hook, event, hwnd, object_id, child_id, thread_id, timestamp):
            token = self._active
            if token is not None and (not token.window or hwnd == token.window or user32.IsChild(token.window, hwnd)):
                token.dirty.set()

        # Keep callbacks alive until all hooks have been removed.
        callbacks = (_HOOKPROC(mouse), _HOOKPROC(keyboard), _EVENTPROC(changed))
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            message = wt.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            module = kernel32.GetModuleHandleW(None)
            for kind, callback in ((14, callbacks[0]), (13, callbacks[1])):
                handle = user32.SetWindowsHookExW(kind, callback, module, 0)
                if not handle:
                    raise OSError("Input hook registration failed")
                hooks.append(handle)
            for event in (0x0003, 0x800B):  # foreground and location changes
                handle = user32.SetWinEventHook(event, event, None, callbacks[2], 0, 0, 0)
                if handle:
                    events.append(handle)
            self.window_events = len(events) == 2
            self._ready.set()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result < 0:
                    raise OSError("Pointer monitor message pump failed")
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            self._error = type(exc).__name__
        finally:
            token = self._active
            if token is not None:
                token.reason = "pointer_monitor_stopped"
                token.interrupted.set()
            for handle in hooks:
                user32.UnhookWindowsHookEx(handle)
            for handle in events:
                user32.UnhookWinEvent(handle)
            self._ready.set()


monitor = PointerMonitor()
