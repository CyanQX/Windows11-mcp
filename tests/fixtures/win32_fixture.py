"""Real Win32 application used by the desktop test-suite (``tests/desktop``).

It is built only from *native* controls (EDIT / BUTTON / COMBOBOX / LISTBOX / trackbar), so UI
Automation exposes it exactly like an ordinary Windows program. Every observable interaction is
appended to a JSON-lines event log. That lets a test assert what the application *actually
received* -- screen coordinates of mouse events, WM_CHAR code units, wheel deltas, control
notifications -- instead of trusting the code under test.

    python win32_fixture.py <event-log.jsonl> [window-title]

The first event is ``{"ev": "ready", ...}`` and carries the window handle plus the screen
rectangles of the interesting controls.
"""

from __future__ import annotations

import ctypes
import json
import sys
import time

import win32api
import win32con
import win32gui

LOG_PATH = sys.argv[1] if len(sys.argv) > 1 else "fixture-events.jsonl"
TITLE = sys.argv[2] if len(sys.argv) > 2 else "WMCP Fixture"
# Safety net: the fixture closes itself after this many seconds even if the test run dies.
LIFETIME_SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
LIFETIME_TIMER = 2

ID_NAME, ID_NOTES, ID_SAVE, ID_ENABLE, ID_COMBO, ID_LIST = 101, 102, 103, 104, 105, 106
ID_SLIDER, ID_DIALOG, ID_STATUS, ID_RADIO_A, ID_RADIO_B, ID_PASSWORD = 107, 108, 109, 110, 111, 112
ID_JOB = 113

TBM_SETRANGE = win32con.WM_USER + 6
TBM_SETPOS = win32con.WM_USER + 5
TBM_GETPOS = win32con.WM_USER
WM_MOUSEHWHEEL = 0x020E  # not exported by win32con
JOB_TIMER = 1
_user32 = ctypes.windll.user32  # win32gui has no SetTimer/KillTimer

_state = {"saves": 0, "hwnd": 0, "canvas": 0, "controls": {}}


def log(**event) -> None:
    event["t"] = time.time()
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _signed(value: int) -> int:
    return ctypes.c_short(value & 0xFFFF).value


def _text(hwnd: int) -> str:
    return win32gui.GetWindowText(hwnd)


def _set_status(text: str) -> None:
    win32gui.SetWindowText(_state["controls"]["status"], text)


# --------------------------------------------------------------------------- canvas window ----
def canvas_proc(hwnd, msg, wparam, lparam):
    if msg in (
        win32con.WM_LBUTTONDOWN,
        win32con.WM_LBUTTONUP,
        win32con.WM_LBUTTONDBLCLK,
        win32con.WM_RBUTTONDOWN,
        win32con.WM_MBUTTONDOWN,
    ):
        cx, cy = _signed(lparam), _signed(lparam >> 16)
        sx, sy = win32gui.ClientToScreen(hwnd, (cx, cy))
        name = {
            win32con.WM_LBUTTONDOWN: "left_down",
            win32con.WM_LBUTTONUP: "left_up",
            win32con.WM_LBUTTONDBLCLK: "left_double",
            win32con.WM_RBUTTONDOWN: "right_down",
            win32con.WM_MBUTTONDOWN: "middle_down",
        }[msg]
        log(ev="mouse", kind=name, x=sx, y=sy)
        if msg in (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONDBLCLK, win32con.WM_RBUTTONDOWN):
            win32gui.SetFocus(hwnd)
        return 0
    if msg in (win32con.WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
        log(
            ev="wheel",
            axis="v" if msg == win32con.WM_MOUSEWHEEL else "h",
            delta=_signed(wparam >> 16),
            x=_signed(lparam),
            y=_signed(lparam >> 16),
        )
        return 0
    if msg == win32con.WM_CHAR:
        log(ev="char", code=wparam)
        return 0
    if msg in (win32con.WM_KEYDOWN, win32con.WM_SYSKEYDOWN):
        log(ev="key", vk=wparam, state="down")
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
    if msg in (win32con.WM_KEYUP, win32con.WM_SYSKEYUP):
        log(ev="key", vk=wparam, state="up")
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
    if msg == win32con.WM_GETDLGCODE:
        return win32con.DLGC_WANTALLKEYS | win32con.DLGC_WANTCHARS
    if msg == win32con.WM_ERASEBKGND:
        return 1
    if msg == win32con.WM_PAINT:
        hdc, paint = win32gui.BeginPaint(hwnd)
        rect = win32gui.GetClientRect(hwnd)
        brush = win32gui.CreateSolidBrush(win32api.RGB(235, 245, 255))
        win32gui.FillRect(hdc, rect, brush)
        win32gui.DeleteObject(brush)
        win32gui.DrawText(
            hdc, "input canvas", -1, rect, win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE
        )
        win32gui.EndPaint(hwnd, paint)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


# ---------------------------------------------------------------------------- main window -----
def main_proc(hwnd, msg, wparam, lparam):
    if msg == win32con.WM_COMMAND:
        ctrl_id, code = wparam & 0xFFFF, (wparam >> 16) & 0xFFFF
        if ctrl_id == ID_SAVE and code == win32con.BN_CLICKED:
            _state["saves"] += 1
            log(ev="click", control="save", count=_state["saves"])
            _set_status(f"saved {_state['saves']}")
        elif ctrl_id in (ID_ENABLE, ID_RADIO_A, ID_RADIO_B) and code == win32con.BN_CLICKED:
            checked = win32gui.SendMessage(lparam, win32con.BM_GETCHECK, 0, 0) == win32con.BST_CHECKED
            log(ev="check", control={ID_ENABLE: "enable", ID_RADIO_A: "radio_a", ID_RADIO_B: "radio_b"}[ctrl_id], checked=checked)
        elif ctrl_id in (ID_NAME, ID_NOTES, ID_PASSWORD) and code == win32con.EN_CHANGE:
            name = {ID_NAME: "name", ID_NOTES: "notes", ID_PASSWORD: "password"}[ctrl_id]
            log(ev="text", control=name, value=_text(lparam))
        elif ctrl_id == ID_COMBO and code == win32con.CBN_SELCHANGE:
            index = win32gui.SendMessage(lparam, win32con.CB_GETCURSEL, 0, 0)
            log(ev="select", control="combo", index=index)
        elif ctrl_id == ID_LIST and code == win32con.LBN_SELCHANGE:
            index = win32gui.SendMessage(lparam, win32con.LB_GETCURSEL, 0, 0)
            log(ev="select", control="list", index=index)
        elif ctrl_id == ID_DIALOG and code == win32con.BN_CLICKED:
            log(ev="dialog_opening")
            win32gui.MessageBox(hwnd, "Modal dialog opened by the fixture.", "Fixture dialog", win32con.MB_OK)
            log(ev="dialog_closed")
        elif ctrl_id == ID_JOB and code == win32con.BN_CLICKED:
            log(ev="click", control="job")
            _set_status("job running")
            _user32.SetTimer(hwnd, JOB_TIMER, 1200, None)
        return 0
    if msg == win32con.WM_TIMER and wparam == JOB_TIMER:
        _user32.KillTimer(hwnd, JOB_TIMER)
        _set_status("job done")
        log(ev="job_done")
        return 0
    if msg == win32con.WM_TIMER and wparam == LIFETIME_TIMER:
        log(ev="lifetime_expired")
        win32gui.DestroyWindow(hwnd)
        return 0
    if msg == win32con.WM_HSCROLL and lparam == _state["controls"].get("slider"):
        log(ev="slider", value=win32gui.SendMessage(lparam, TBM_GETPOS, 0, 0))
        return 0
    if msg == win32con.WM_CLOSE:
        log(ev="closing")
        win32gui.DestroyWindow(hwnd)
        return 0
    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def _child(cls, text, style, x, y, w, h, ctrl_id=0, ex_style=0):
    return win32gui.CreateWindowEx(
        ex_style, cls, text, win32con.WS_CHILD | win32con.WS_VISIBLE | style,
        x, y, w, h, _state["hwnd"], ctrl_id, win32api.GetModuleHandle(None), None,
    )  # fmt: skip


def _label(text, x, y, w=90, h=20):
    return _child("STATIC", text, 0, x, y + 3, w, h)


def build() -> int:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    ctypes.windll.comctl32.InitCommonControls()
    instance = win32api.GetModuleHandle(None)

    canvas_class = win32gui.WNDCLASS()
    canvas_class.hInstance = instance
    canvas_class.lpszClassName = "WmcpFixtureCanvas"
    canvas_class.lpfnWndProc = canvas_proc
    canvas_class.style = win32con.CS_DBLCLKS
    canvas_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    win32gui.RegisterClass(canvas_class)

    main_class = win32gui.WNDCLASS()
    main_class.hInstance = instance
    main_class.lpszClassName = "WmcpFixtureMain"
    main_class.lpfnWndProc = main_proc
    main_class.hbrBackground = win32con.COLOR_BTNFACE + 1
    main_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    win32gui.RegisterClass(main_class)

    # Topmost, so injected test input can never land in the user's own windows.
    hwnd = win32gui.CreateWindowEx(
        win32con.WS_EX_TOPMOST, "WmcpFixtureMain", TITLE, win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE,
        100, 100, 800, 640, 0, 0, instance, None,
    )  # fmt: skip
    _state["hwnd"] = hwnd
    controls = _state["controls"]

    _label("Name", 16, 14)
    controls["name"] = _child("EDIT", "", win32con.WS_BORDER | win32con.ES_AUTOHSCROLL | win32con.WS_TABSTOP, 110, 14, 260, 24, ID_NAME)
    _label("Password", 16, 48)
    controls["password"] = _child("EDIT", "", win32con.WS_BORDER | win32con.ES_PASSWORD | win32con.ES_AUTOHSCROLL | win32con.WS_TABSTOP, 110, 48, 260, 24, ID_PASSWORD)
    _label("Notes", 16, 82)
    controls["notes"] = _child("EDIT", "", win32con.WS_BORDER | win32con.ES_MULTILINE | win32con.ES_AUTOVSCROLL | win32con.WS_VSCROLL | win32con.WS_TABSTOP, 110, 82, 260, 90, ID_NOTES)
    controls["save"] = _child("BUTTON", "Save", win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP, 16, 190, 110, 30, ID_SAVE)
    controls["enable"] = _child("BUTTON", "Enable feature", win32con.BS_AUTOCHECKBOX | win32con.WS_TABSTOP, 140, 190, 140, 30, ID_ENABLE)
    controls["radio_a"] = _child("BUTTON", "Option A", win32con.BS_AUTORADIOBUTTON | win32con.WS_GROUP | win32con.WS_TABSTOP, 16, 230, 110, 24, ID_RADIO_A)
    controls["radio_b"] = _child("BUTTON", "Option B", win32con.BS_AUTORADIOBUTTON, 140, 230, 110, 24, ID_RADIO_B)
    _label("Fruit", 16, 270)
    controls["combo"] = _child("COMBOBOX", "", win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL | win32con.WS_TABSTOP, 110, 270, 200, 200, ID_COMBO)
    for item in ("Apple", "Banana", "Cherry", "Durian"):
        win32gui.SendMessage(controls["combo"], win32con.CB_ADDSTRING, 0, item)
    _label("Items", 16, 310)
    controls["list"] = _child("LISTBOX", "", win32con.WS_BORDER | win32con.WS_VSCROLL | win32con.LBS_NOTIFY | win32con.WS_TABSTOP, 110, 310, 200, 120, ID_LIST)
    for index in range(1, 61):
        win32gui.SendMessage(controls["list"], win32con.LB_ADDSTRING, 0, f"Item {index:02d}")
    _label("Volume", 16, 446)
    controls["slider"] = _child("msctls_trackbar32", "", 0x0001 | win32con.WS_TABSTOP, 110, 442, 220, 32, ID_SLIDER)  # TBS_AUTOTICKS
    win32gui.SendMessage(controls["slider"], TBM_SETRANGE, 1, (100 << 16) | 0)
    win32gui.SendMessage(controls["slider"], TBM_SETPOS, 1, 20)
    controls["dialog"] = _child("BUTTON", "Open dialog", win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP, 16, 490, 130, 30, ID_DIALOG)
    controls["job"] = _child("BUTTON", "Start job", win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP, 160, 490, 110, 30, ID_JOB)
    controls["status"] = _child("STATIC", "idle", 0, 290, 496, 200, 22, ID_STATUS)

    _state["canvas"] = win32gui.CreateWindowEx(
        win32con.WS_EX_CLIENTEDGE, "WmcpFixtureCanvas", "", win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
        400, 14, 360, 300, hwnd, 200, instance, None,
    )  # fmt: skip
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass  # foreground lock; the tests activate the window themselves
    _user32.SetTimer(hwnd, LIFETIME_TIMER, int(LIFETIME_SECONDS * 1000), None)
    return hwnd


def _screen_rect(hwnd: int) -> list[int]:
    return list(win32gui.GetWindowRect(hwnd))


def main() -> None:
    open(LOG_PATH, "w", encoding="utf-8").close()
    hwnd = build()
    log(
        ev="ready",
        hwnd=hwnd,
        pid=win32api.GetCurrentProcessId(),
        window=_screen_rect(hwnd),
        canvas=_screen_rect(_state["canvas"]),
        controls={name: _screen_rect(handle) for name, handle in _state["controls"].items()},
    )
    win32gui.PumpMessages()


if __name__ == "__main__":
    main()
