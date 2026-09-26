"""Real mouse and keyboard input through ``SendInput``.

Everything here produces genuine input events -- the same kind a physical mouse or keyboard
produces -- so applications react exactly as they would to a person.

Design points (each one fixes a problem of the old ``mouse_event``/``keybd_event`` path):

* **Exact positions on every monitor.** Moves use ``MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK``
  normalised against the *virtual* screen, so monitors left of / above the primary one (negative
  coordinates) work, and the final cursor position is read back and corrected if needed.
* **Atomic chords.** A shortcut is sent as ONE ``SendInput`` batch (modifiers down, keys, modifiers
  up), so it cannot be interleaved with other input and a modifier can never be left pressed.
* **Correct extended-key flags.** Only keys that really are extended (arrows, Insert/Delete,
  Home/End, PgUp/PgDn, Win, ...) carry ``KEYEVENTF_EXTENDEDKEY``; the old code flagged every key.
* **Unicode text that survives an IME.** Text is injected as ``KEYEVENTF_UNICODE`` UTF-16 code
  units, so Chinese/Japanese input methods do not intercept it and characters outside the BMP
  (emoji) are sent as proper surrogate pairs instead of being truncated.
* **Honest failures.** If Windows inserts fewer events than requested (UIPI: the target runs
  elevated, the secure desktop is up, the workstation is locked) an :class:`InputBlockedError`
  is raised instead of pretending the input happened.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

# --------------------------------------------------------------------------------------------
# Win32 structures
# --------------------------------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

WHEEL_DELTA = 120

# Tags every event we inject, so they can be told apart from physical input if ever needed.
EXTRA_INFO = 0x574D4350  # "WMCP"

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wt.UINT
_user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
_user32.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
_user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
_user32.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
_user32.MapVirtualKeyW.restype = wt.UINT
_user32.VkKeyScanW.argtypes = (wt.WCHAR,)
_user32.VkKeyScanW.restype = ctypes.c_short
_user32.GetDoubleClickTime.restype = wt.UINT
_user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_user32.GetAsyncKeyState.restype = ctypes.c_short


class InputBlockedError(RuntimeError):
    """Windows refused (part of) the input -- nothing should be assumed to have happened."""


def _send(inputs: Sequence[INPUT]) -> None:
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    inserted = _user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if inserted != len(inputs):
        error = ctypes.get_last_error()
        raise InputBlockedError(
            f"Windows accepted only {inserted} of {len(inputs)} input events (error {error}). "
            "Input is blocked when the target window runs as administrator while this server "
            "does not, when a UAC/secure-desktop prompt is showing, or when the screen is locked."
        )


# --------------------------------------------------------------------------------------------
# Mouse
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VirtualScreen:
    left: int
    top: int
    width: int
    height: int


def virtual_screen() -> VirtualScreen:
    return VirtualScreen(
        left=_user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        top=_user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        width=max(1, _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        height=max(1, _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    )


def to_absolute(x: int, y: int, screen: VirtualScreen | None = None) -> tuple[int, int]:
    """Map a virtual-desktop pixel to SendInput's 0..65535 absolute space."""
    screen = screen or virtual_screen()
    nx = round((x - screen.left) * 65535 / max(screen.width - 1, 1))
    ny = round((y - screen.top) * 65535 / max(screen.height - 1, 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def cursor_position() -> tuple[int, int]:
    point = wt.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    event = INPUT(type=INPUT_MOUSE)
    event.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, EXTRA_INFO)
    return event


def _move_event(x: int, y: int, screen: VirtualScreen) -> INPUT:
    nx, ny = to_absolute(x, y, screen)
    return _mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)  # smoothstep: gentle start and stop, like a hand


def move_to(x: int, y: int, duration: float = 0.0) -> tuple[int, int]:
    """Move the pointer to (x, y); with ``duration`` > 0 it glides there through real move events.

    Returns the final cursor position (always exactly (x, y) unless Windows blocks the cursor).
    """
    x, y = int(x), int(y)
    screen = virtual_screen()
    if duration > 0:
        start_x, start_y = cursor_position()
        distance = math.hypot(x - start_x, y - start_y)
        steps = max(2, min(60, int(duration / 0.008), int(distance / 6) + 2))
        for step in range(1, steps):
            t = _ease(step / steps)
            _send([_move_event(round(start_x + (x - start_x) * t), round(start_y + (y - start_y) * t), screen)])
            time.sleep(duration / steps)
    _send([_move_event(x, y, screen)])
    final = cursor_position()
    if final != (x, y):
        # Absolute mapping can be off by a pixel on odd virtual-screen sizes; pin it exactly.
        _user32.SetCursorPos(x, y)
        final = cursor_position()
    return final


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def _button_flags(button: str) -> tuple[int, int]:
    try:
        return _BUTTONS[button]
    except KeyError:
        raise ValueError(f"button must be one of {sorted(_BUTTONS)}, got {button!r}") from None


def click(x: int, y: int, button: str = "left", count: int = 1, glide: float = 0.04) -> tuple[int, int]:
    """Real click(s) at (x, y). ``count`` 0 only moves (hover), 2 is a double click, 3 a triple."""
    down, up = _button_flags(button)
    if count < 0 or count > 3:
        raise ValueError("clicks must be between 0 and 3")
    start = cursor_position()
    final = move_to(x, y, duration=glide if math.dist(start, (x, y)) > 4 else 0.0)
    if count == 0:
        return final
    gap = min(0.06, _user32.GetDoubleClickTime() / 5000.0)
    for index in range(count):
        _send([_mouse(down), _mouse(up)])
        if index + 1 < count:
            time.sleep(gap)
    return final


def press(button: str = "left") -> None:
    _send([_mouse(_button_flags(button)[0])])


def release(button: str = "left") -> None:
    _send([_mouse(_button_flags(button)[1])])


def drag(
    start: tuple[int, int],
    end: tuple[int, int],
    button: str = "left",
    duration: float = 0.35,
) -> None:
    """Press at ``start``, glide to ``end`` through real move events, release. Always releases."""
    move_to(*start)
    press(button)
    try:
        time.sleep(0.05)
        move_to(*end, duration=max(duration, 0.05))
        time.sleep(0.05)
    finally:
        release(button)


def wheel(notches: int, horizontal: bool = False, interval: float = 0.03) -> None:
    """Scroll by ``notches`` wheel detents. Vertical: positive = up. Horizontal: positive = right."""
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    step = WHEEL_DELTA if notches > 0 else -WHEEL_DELTA
    for _ in range(abs(int(notches))):
        _send([_mouse(flag, data=step)])
        time.sleep(interval)


# --------------------------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------------------------

VK_BACK, VK_TAB, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x08, 0x09, 0x0D, 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C

KEY_NAMES: dict[str, int] = {
    "backspace": 0x08, "back": 0x08, "bs": 0x08,
    "tab": 0x09,
    "clear": 0x0C,
    "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12, "option": 0x12,
    "pause": 0x13, "break": 0x13,
    "capslock": 0x14, "caps": 0x14,
    "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "spacebar": 0x20,
    "pageup": 0x21, "pgup": 0x21, "prior": 0x21,
    "pagedown": 0x22, "pgdn": 0x22, "next": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "prtscr": 0x2C, "snapshot": 0x2C,
    "insert": 0x2D, "ins": 0x2D,
    "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "windows": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "cmd": 0x5B, "command": 0x5B,
    "super": 0x5B, "meta": 0x5B, "start": 0x5B,
    "apps": 0x5D, "contextmenu": 0x5D, "application": 0x5D,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63, "numpad4": 0x64,
    "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67, "numpad8": 0x68, "numpad9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "separator": 0x6C, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "numlock": 0x90, "scrolllock": 0x91,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "lcontrol": 0xA2, "rctrl": 0xA3, "rcontrol": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5, "altgr": 0xA5,
    "browserback": 0xA6, "browserforward": 0xA7, "browserrefresh": 0xA8, "browserstop": 0xA9,
    "browsersearch": 0xAA, "browserfavorites": 0xAB, "browserhome": 0xAC,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "medianext": 0xB0, "mediaprev": 0xB1, "mediaprevious": 0xB1, "mediastop": 0xB2,
    "mediaplaypause": 0xB3, "playpause": 0xB3,
    "plus": 0xBB,  # the '=/+' key; lets "ctrl+plus" be written without clashing with the '+' separator
    "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
}
KEY_NAMES.update({f"f{n}": 0x6F + n for n in range(1, 25)})

MODIFIERS = {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

# Keys whose scan code carries the 0xE0 prefix; they need KEYEVENTF_EXTENDEDKEY.
EXTENDED_KEYS = {
    0x03, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
    0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5,
    *range(0xA6, 0xB8),
}


def _key_event(vk: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = _user32.MapVirtualKeyW(vk, 0) & 0xFF  # MAPVK_VK_TO_VSC
    event = INPUT(type=INPUT_KEYBOARD)
    event.u.ki = KEYBDINPUT(vk, scan, flags, 0, EXTRA_INFO)
    return event


def _unicode_events(code_unit: int) -> list[INPUT]:
    down = INPUT(type=INPUT_KEYBOARD)
    down.u.ki = KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, EXTRA_INFO)
    up = INPUT(type=INPUT_KEYBOARD)
    up.u.ki = KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, EXTRA_INFO)
    return [down, up]


def utf16_units(text: str) -> list[int]:
    """UTF-16 code units of ``text`` -- characters outside the BMP become surrogate pairs."""
    data = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


@dataclass(frozen=True)
class Chord:
    """One key combination: modifiers held while ``keys`` are pressed in order."""

    modifiers: tuple[int, ...]
    keys: tuple[int, ...]


def _vk_for_character(char: str) -> tuple[int, bool]:
    """(virtual key, needs shift) for a printable character on the current keyboard layout."""
    if "a" <= char.lower() <= "z" and len(char) == 1:
        return ord(char.upper()), char.isupper()
    if char.isdigit() and len(char) == 1 and char.isascii():
        return ord(char), False
    result = _user32.VkKeyScanW(char)
    if result == -1:
        raise ValueError(
            f"key {char!r} does not exist on the current keyboard layout; use the Type tool for text"
        )
    return result & 0xFF, bool(result & 0x0100)


def parse_chord(spec: str) -> Chord:
    """Parse ``"ctrl+shift+s"``, ``"alt+f4"``, ``"win"``, ``"ctrl++"`` (ctrl and the '+' key)."""
    text = spec.strip()
    if not text:
        raise ValueError("empty key combination")
    parts = text.split("+")
    if text.endswith("++"):
        parts = parts[:-2] + ["+"]
    elif text == "+":
        parts = ["+"]
    names = [part.strip() for part in parts]
    if any(not name for name in names):
        raise ValueError(f"malformed key combination {spec!r}")
    modifiers: list[int] = []
    keys: list[int] = []
    for index, name in enumerate(names):
        lowered = name.lower().replace(" ", "").replace("_", "")
        vk = KEY_NAMES.get(lowered)
        if vk is None and len(name) == 1:
            vk, needs_shift = _vk_for_character(name)
            if needs_shift and VK_SHIFT not in modifiers:
                modifiers.append(VK_SHIFT)
        if vk is None:
            raise ValueError(
                f"unknown key {name!r} in {spec!r}. Use names like ctrl, shift, alt, win, enter, tab, "
                "esc, space, backspace, delete, home, end, pageup, pagedown, up, down, left, right, f1-f24, "
                "or a single character."
            )
        is_last = index == len(names) - 1
        if vk in MODIFIERS and not is_last:
            if vk not in modifiers:
                modifiers.append(vk)
        else:
            keys.append(vk)  # a lone modifier ("win", "alt") is pressed and released as a key
    return Chord(tuple(modifiers), tuple(keys))


def parse_sequence(spec: str) -> list[Chord]:
    """Space-separated chords, e.g. ``"ctrl+k ctrl+s"`` (VS Code style) or just ``"ctrl+c"``."""
    tokens = spec.split()
    if not tokens:
        raise ValueError("empty key combination")
    return [parse_chord(token) for token in tokens]


def chord_events(chord: Chord) -> list[INPUT]:
    events = [_key_event(vk) for vk in chord.modifiers]
    for vk in chord.keys:
        events += [_key_event(vk), _key_event(vk, up=True)]
    events += [_key_event(vk, up=True) for vk in reversed(chord.modifiers)]
    return events


def press_keys(spec: str, repeat: int = 1, interval: float = 0.03) -> list[Chord]:
    """Press a key combination (or a space-separated sequence of them) ``repeat`` times."""
    if repeat < 1 or repeat > 100:
        raise ValueError("repeat must be between 1 and 100")
    chords = parse_sequence(spec)
    for _ in range(repeat):
        for chord in chords:
            _send(chord_events(chord))  # one atomic batch per chord
            time.sleep(interval)
    return chords


@contextmanager
def hold(*key_names: str) -> Iterator[None]:
    """Hold keys (e.g. ``hold("ctrl")`` for ctrl-click multi-selection); always released."""
    vks = []
    for name in key_names:
        vk = KEY_NAMES.get(name.lower())
        if vk is None:
            raise ValueError(f"unknown key {name!r}")
        vks.append(vk)
    _send([_key_event(vk) for vk in vks])
    try:
        yield
    finally:
        _send([_key_event(vk, up=True) for vk in reversed(vks)])


def text_events(text: str) -> list[INPUT]:
    """Input events that type ``text`` exactly: Unicode units, Enter for newlines, Tab for tabs."""
    events: list[INPUT] = []
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\n":
            events += [_key_event(VK_RETURN), _key_event(VK_RETURN, up=True)]
        elif char == "\t":
            events += [_key_event(VK_TAB), _key_event(VK_TAB, up=True)]
        else:
            for unit in utf16_units(char):
                events += _unicode_events(unit)
    return events


def type_text(text: str, chunk_chars: int = 32, pause: float = 0.01) -> int:
    """Type ``text`` through real key events. Returns the number of characters sent."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for start in range(0, len(normalized), chunk_chars):
        _send(text_events(normalized[start : start + chunk_chars]))
        time.sleep(pause)
    return len(normalized)


def modifiers_down() -> list[str]:
    """Names of modifier keys Windows currently reports as pressed (diagnostics)."""
    names = {VK_SHIFT: "shift", VK_CONTROL: "ctrl", VK_MENU: "alt", VK_LWIN: "win", VK_RWIN: "win"}
    return sorted({label for vk, label in names.items() if _user32.GetAsyncKeyState(vk) & 0x8000})
