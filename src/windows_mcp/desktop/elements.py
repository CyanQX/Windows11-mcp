"""Live UI elements: find them, keep stable references, act on them, verify what happened.

This is the layer that turns "the model looked at the screen" into "the model operated the real
application":

* :func:`search` queries one or more real windows through UI Automation with a single cached
  cross-process call and ranks the matches (exact name > prefix > substring > fuzzy).
* :class:`ElementRegistry` hands out short references (``e1``, ``e2`` ...). A reference is never a
  frozen coordinate: before every action the element is re-read live, re-located if the window
  was rebuilt, scrolled into view and hit-tested, so a moved window or a re-laid-out dialog does
  not turn a click into a click on something else.
* :func:`act` performs semantic actions (click, invoke, toggle, select, expand, set_value, ...)
  through native UIA patterns or real mouse/keyboard input, then reads the element back and
  reports whether the intended state was reached (``verified``).
* :func:`observe` / :func:`describe_changes` report the side effects a person would notice:
  windows that opened or closed, title changes, foreground and keyboard-focus changes.
* :func:`ensure_foreground` is the keyboard safety interlock: keystrokes are only sent after the
  target window is confirmed to be in the foreground.

All functions must run on the desktop thread (see :mod:`windows_mcp.runtime`).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import psutil
import win32con
import win32gui
import win32process
from _ctypes import COMError
from thefuzz import fuzz

from windows_mcp.desktop import native_input
from windows_mcp.uia.core import _AutomationClient
from windows_mcp.uia.enums import (
    ControlTypeNames,
    ExpandCollapseState,
    PatternId,
    PropertyId,
    ToggleState,
    TreeScope,
)

logger = logging.getLogger(__name__)

P = PropertyId

# --------------------------------------------------------------------------------------------
# Errors -- each one tells the model what to do next.
# --------------------------------------------------------------------------------------------


class ElementError(RuntimeError):
    """Base class for element resolution/action failures."""


class ElementNotFound(ElementError):
    pass


class AmbiguousTarget(ElementError):
    pass


class StaleTarget(ElementError):
    pass


class OccludedError(ElementError):
    pass


class NotEnabledError(ElementError):
    pass


class FocusLostError(ElementError):
    pass


# --------------------------------------------------------------------------------------------
# UIA plumbing
# --------------------------------------------------------------------------------------------


def _client():
    return _AutomationClient.instance().IUIAutomation


def _module():
    return _AutomationClient.instance().UIAutomationCore


_PATTERN_INTERFACES = {
    PatternId.InvokePattern: "IUIAutomationInvokePattern",
    PatternId.ValuePattern: "IUIAutomationValuePattern",
    PatternId.RangeValuePattern: "IUIAutomationRangeValuePattern",
    PatternId.ExpandCollapsePattern: "IUIAutomationExpandCollapsePattern",
    PatternId.SelectionItemPattern: "IUIAutomationSelectionItemPattern",
    PatternId.TogglePattern: "IUIAutomationTogglePattern",
    PatternId.ScrollItemPattern: "IUIAutomationScrollItemPattern",
    PatternId.ScrollPattern: "IUIAutomationScrollPattern",
    PatternId.WindowPattern: "IUIAutomationWindowPattern",
}

# pattern availability property -> short capability name shown to the model
_CAPABILITIES = (
    (P.IsInvokePatternAvailableProperty, "invoke"),
    (P.IsTogglePatternAvailableProperty, "toggle"),
    (P.IsSelectionItemPatternAvailableProperty, "select"),
    (P.IsExpandCollapsePatternAvailableProperty, "expand"),
    (P.IsValuePatternAvailableProperty, "value"),
    (P.IsRangeValuePatternAvailableProperty, "range"),
    (P.IsScrollPatternAvailableProperty, "scroll"),
    (P.IsScrollItemPatternAvailableProperty, "scroll_into_view"),
    (P.IsSelectionPatternAvailableProperty, "selection"),
    (P.IsTextPatternAvailableProperty, "text"),
)

_STATE_PROPERTIES = (
    P.ValueValueProperty,
    P.ValueIsReadOnlyProperty,
    P.ToggleToggleStateProperty,
    P.SelectionItemIsSelectedProperty,
    P.ExpandCollapseExpandCollapseStateProperty,
    P.RangeValueValueProperty,
    P.RangeValueMinimumProperty,
    P.RangeValueMaximumProperty,
    P.RangeValueIsReadOnlyProperty,
)

_BASIC_PROPERTIES = (
    P.NameProperty,
    P.ControlTypeProperty,
    P.LocalizedControlTypeProperty,
    P.AutomationIdProperty,
    P.ClassNameProperty,
    P.BoundingRectangleProperty,
    P.IsEnabledProperty,
    P.IsOffscreenProperty,
    P.HasKeyboardFocusProperty,
    P.IsPasswordProperty,
    P.ProcessIdProperty,
    P.FrameworkIdProperty,
    P.RuntimeIdProperty,
)

_SCALARS = (str, int, float, bool)


def _read(raw: Any, prop: int, cached: bool) -> Any:
    try:
        value = raw.GetCachedPropertyValue(prop) if cached else raw.GetCurrentPropertyValue(prop)
    except (COMError, OSError, ValueError, TypeError):
        return None
    if prop == P.RuntimeIdProperty:
        return tuple(value) if isinstance(value, (tuple, list)) else None
    # Unsupported properties come back as a sentinel COM object -- treat them as missing.
    return value if isinstance(value, _SCALARS) else None


def _rect_of(raw: Any, cached: bool) -> tuple[int, int, int, int]:
    try:
        rect = raw.CachedBoundingRectangle if cached else raw.CurrentBoundingRectangle
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except (COMError, OSError, ValueError, AttributeError):
        return 0, 0, 0, 0


def get_pattern(raw: Any, pattern_id: int) -> Any | None:
    try:
        unknown = raw.GetCurrentPattern(pattern_id)
    except (COMError, OSError):
        return None
    if not unknown:
        return None
    try:
        return unknown.QueryInterface(getattr(_module(), _PATTERN_INTERFACES[pattern_id]))
    except (COMError, OSError, AttributeError, KeyError):
        return None


def call_with_timeout(func: Callable[[], Any], timeout: float = 5.0) -> tuple[bool, Any]:
    """Run a UIA pattern call on a helper MTA thread.

    Some providers only return from Invoke/Toggle/SetValue after the application handled the
    action -- e.g. after a modal dialog it opened is closed again. Running the call on a helper
    thread keeps the desktop thread responsive. Returns ``(finished, result)``; re-raises errors.
    """
    done = threading.Event()
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            import comtypes

            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=runner, name="wmcp-pattern-call", daemon=True).start()
    finished = done.wait(timeout)
    if finished and "error" in box:
        raise box["error"]
    return finished, box.get("value")


# --------------------------------------------------------------------------------------------
# Control types
# --------------------------------------------------------------------------------------------

_TYPE_ALIASES = {
    "btn": "Button", "edit": "Edit", "textbox": "Edit", "input": "Edit", "field": "Edit",
    "textfield": "Edit", "entry": "Edit", "label": "Text", "static": "Text",
    "check": "CheckBox", "checkbox": "CheckBox", "radio": "RadioButton", "radiobutton": "RadioButton",
    "combo": "ComboBox", "combobox": "ComboBox", "dropdown": "ComboBox", "select": "ComboBox",
    "listbox": "List", "item": "ListItem", "option": "ListItem", "listitem": "ListItem",
    "menuitem": "MenuItem", "tab": "TabItem", "tabitem": "TabItem", "tabs": "Tab", "tabcontrol": "Tab",
    "link": "Hyperlink", "trackbar": "Slider", "spin": "Spinner", "updown": "Spinner",
    "treeitem": "TreeItem", "node": "TreeItem", "dialog": "Window", "grid": "DataGrid",
    "datagrid": "DataGrid", "row": "DataItem", "dataitem": "DataItem", "column": "HeaderItem",
    "progress": "ProgressBar", "doc": "Document",
}
_TYPE_BY_SHORT = {name[: -len("Control")].lower(): type_id for type_id, name in ControlTypeNames.items()}


def short_type(type_id: int | None) -> str:
    name = ControlTypeNames.get(type_id or 0, "UnknownControl")
    return name[: -len("Control")] if name.endswith("Control") else name


def normalize_control_type(value: str) -> int:
    key = value.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if key.endswith("control") and key != "control":
        key = key[: -len("control")]
    key = _TYPE_ALIASES.get(key, key).lower()
    if key in _TYPE_BY_SHORT:
        return _TYPE_BY_SHORT[key]
    choices = ", ".join(sorted(_TYPE_BY_SHORT))
    raise ValueError(f"unknown control_type {value!r}; use one of: {choices}")


# --------------------------------------------------------------------------------------------
# Elements
# --------------------------------------------------------------------------------------------


@dataclass
class Element:
    raw: Any = field(repr=False)
    name: str
    control_type: str
    control_type_id: int
    localized_type: str
    automation_id: str
    class_name: str
    rect: tuple[int, int, int, int]
    enabled: bool
    offscreen: bool
    focused: bool
    password: bool
    pid: int
    framework: str
    runtime_id: tuple | None
    capabilities: frozenset[str]
    state: dict[str, Any]
    window_handle: int = 0
    window_title: str = ""
    ref: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2

    @property
    def has_area(self) -> bool:
        left, top, right, bottom = self.rect
        return right > left and bottom > top

    @property
    def label(self) -> str:
        name = self.name if len(self.name) <= 60 else self.name[:57] + "..."
        return f'{self.control_type} "{name}"' if name else self.control_type

    def state_text(self) -> str:
        parts = []
        state = self.state
        if "value" in state:
            parts.append(f'value="{_clip(state["value"], 60)}"')
        elif self.password:
            parts.append("password")
        if "toggle" in state:
            parts.append(f"toggle={state['toggle']}")
        if state.get("selected"):
            parts.append("selected")
        if "expanded" in state and state["expanded"] != "leaf":
            parts.append(state["expanded"])
        if "range" in state:
            value, low, high = state["range"]
            parts.append(f"range={_fmt(value)} ({_fmt(low)}..{_fmt(high)})")
        if state.get("read_only"):
            parts.append("read-only")
        if not self.enabled:
            parts.append("DISABLED")
        if self.offscreen:
            parts.append("offscreen")
        if self.focused:
            parts.append("focused")
        return " ".join(parts)


def _clip(value: Any, limit: int) -> str:
    text = str(value).replace("\r\n", "\\n").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:.4g}" if isinstance(value, float) else str(value)


_TOGGLE_NAMES = {ToggleState.Off: "off", ToggleState.On: "on", ToggleState.Indeterminate: "indeterminate"}
_EXPAND_NAMES = {
    ExpandCollapseState.Collapsed: "collapsed",
    ExpandCollapseState.Expanded: "expanded",
    ExpandCollapseState.PartiallyExpanded: "partially-expanded",
    ExpandCollapseState.LeafNode: "leaf",
}


def element_from_raw(
    raw: Any, *, cached: bool = False, window_handle: int = 0, window_title: str = ""
) -> Element:
    values = {prop: _read(raw, prop, cached) for prop in _BASIC_PROPERTIES}
    capabilities = frozenset(name for prop, name in _CAPABILITIES if _read(raw, prop, cached))
    password = bool(values[P.IsPasswordProperty])
    state: dict[str, Any] = {}
    if "value" in capabilities:
        if not password:
            value = _read(raw, P.ValueValueProperty, cached)
            if value is not None:
                state["value"] = value
        read_only = _read(raw, P.ValueIsReadOnlyProperty, cached)
        if read_only is not None:
            state["read_only"] = bool(read_only)
    if "toggle" in capabilities:
        toggle = _read(raw, P.ToggleToggleStateProperty, cached)
        if toggle is not None:
            state["toggle"] = _TOGGLE_NAMES.get(toggle, str(toggle))
    if "select" in capabilities:
        selected = _read(raw, P.SelectionItemIsSelectedProperty, cached)
        if selected is not None:
            state["selected"] = bool(selected)
    if "expand" in capabilities:
        expanded = _read(raw, P.ExpandCollapseExpandCollapseStateProperty, cached)
        if expanded is not None:
            state["expanded"] = _EXPAND_NAMES.get(expanded, str(expanded))
    if "range" in capabilities:
        value = _read(raw, P.RangeValueValueProperty, cached)
        low = _read(raw, P.RangeValueMinimumProperty, cached)
        high = _read(raw, P.RangeValueMaximumProperty, cached)
        if value is not None:
            state["range"] = (value, low, high)
        if _read(raw, P.RangeValueIsReadOnlyProperty, cached):
            state["read_only"] = True
    type_id = values[P.ControlTypeProperty] or 0
    return Element(
        raw=raw,
        name=(values[P.NameProperty] or "").strip(),
        control_type=short_type(type_id),
        control_type_id=type_id,
        localized_type=(values[P.LocalizedControlTypeProperty] or "").strip(),
        automation_id=values[P.AutomationIdProperty] or "",
        class_name=values[P.ClassNameProperty] or "",
        rect=_rect_of(raw, cached),
        enabled=bool(values[P.IsEnabledProperty]) if values[P.IsEnabledProperty] is not None else True,
        offscreen=bool(values[P.IsOffscreenProperty]),
        focused=bool(values[P.HasKeyboardFocusProperty]),
        password=password,
        pid=int(values[P.ProcessIdProperty] or 0),
        framework=values[P.FrameworkIdProperty] or "",
        runtime_id=values[P.RuntimeIdProperty],
        capabilities=capabilities,
        state=state,
        window_handle=window_handle,
        window_title=window_title,
    )


def _cache_request():
    request = _client().CreateCacheRequest()
    for prop in (*_BASIC_PROPERTIES, *(prop for prop, _ in _CAPABILITIES), *_STATE_PROPERTIES):
        request.AddProperty(prop)
    return request


def refresh(element: Element) -> Element | None:
    """Re-read the element live. ``None`` if it no longer exists."""
    try:
        name = element.raw.CurrentName  # cheap liveness probe -- raises for dead elements
    except (COMError, OSError):
        return None
    if name is None:
        return None
    fresh = element_from_raw(
        element.raw, cached=False, window_handle=element.window_handle, window_title=element.window_title
    )
    fresh.ref = element.ref
    return fresh


# --------------------------------------------------------------------------------------------
# Top-level windows
# --------------------------------------------------------------------------------------------

_dwmapi = ctypes.WinDLL("dwmapi")
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.WindowFromPoint.argtypes = (wt.POINT,)
_user32.WindowFromPoint.restype = wt.HWND
GA_ROOT = 2


@dataclass(frozen=True)
class TopWindow:
    handle: int
    title: str
    pid: int
    class_name: str
    minimized: bool

    @property
    def label(self) -> str:
        return f'"{self.title}" (handle 0x{self.handle:X}, {process_name(self.pid)})'


def _is_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    try:
        _dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except OSError:
        return False
    return bool(cloaked.value)


_process_names: dict[int, str] = {}


def process_name(pid: int) -> str:
    if pid not in _process_names:
        try:
            _process_names[pid] = psutil.Process(pid).name()
        except (psutil.Error, OSError):
            return f"pid {pid}"
    return _process_names[pid]


def window_info(hwnd: int) -> TopWindow | None:
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return TopWindow(
            handle=int(hwnd),
            title=win32gui.GetWindowText(hwnd),
            pid=int(pid),
            class_name=win32gui.GetClassName(hwnd),
            minimized=bool(win32gui.IsIconic(hwnd)),
        )
    except win32gui.error:
        return None


def top_windows() -> list[TopWindow]:
    """Visible, titled, non-cloaked top-level windows in z-order (topmost first)."""
    handles: list[int] = []

    def collect(hwnd: int, _: Any) -> bool:
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowTextLength(hwnd) > 0 and not _is_cloaked(hwnd):
                handles.append(hwnd)
        except win32gui.error:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except win32gui.error:
        pass
    own_pid = os.getpid()
    windows = [info for info in (window_info(h) for h in handles) if info and info.pid != own_pid]
    return windows


def root_of(hwnd: int) -> int:
    try:
        return int(win32gui.GetAncestor(hwnd, GA_ROOT)) if hwnd else 0
    except win32gui.error:
        return 0


def root_at(x: int, y: int) -> int:
    return root_of(int(_user32.WindowFromPoint(wt.POINT(int(x), int(y))) or 0))


def foreground_root() -> int:
    return root_of(int(win32gui.GetForegroundWindow() or 0))


def _parse_handle(text: str) -> int | None:
    token = text.strip().lower()
    try:
        if token.startswith("0x"):
            return int(token, 16)
        if token.isdigit():
            return int(token)
    except ValueError:
        return None
    return None


def resolve_windows(spec: str | int | None) -> list[TopWindow]:
    """Find the window(s) meant by ``spec``.

    ``None``/"active"/"foreground" -> the foreground window; "*"/"all" -> every visible window;
    "taskbar"/"desktop"; a handle (int, "0x1A2B", "1234"); otherwise a title (exact, then
    substring, then process name such as "notepad" or "notepad.exe", then fuzzy).
    """
    if isinstance(spec, int):
        info = window_info(root_of(spec) or spec)
        return [info] if info else []
    text = (spec or "").strip()
    lowered = text.lower()
    if lowered in {"", "active", "foreground", "current", "focused"}:
        info = window_info(foreground_root())
        return [info] if info else []
    if lowered in {"*", "all"}:
        return top_windows()
    if lowered == "taskbar":
        info = window_info(win32gui.FindWindow("Shell_TrayWnd", None))
        return [info] if info else []
    if lowered == "desktop":
        info = window_info(win32gui.FindWindow("Progman", None))
        return [info] if info else []
    handle = _parse_handle(text)
    if handle is not None:
        info = window_info(root_of(handle) or handle)
        return [info] if info else []
    windows = top_windows()
    exact = [w for w in windows if w.title.lower() == lowered]
    if exact:
        return exact
    partial = [w for w in windows if lowered in w.title.lower()]
    if partial:
        return partial
    by_process = [
        w for w in windows if process_name(w.pid).lower() in {lowered, f"{lowered}.exe"}
    ]
    if by_process:
        return by_process
    scored = sorted(
        ((fuzz.partial_ratio(lowered, w.title.lower()), w) for w in windows), key=lambda pair: -pair[0]
    )
    return [w for score, w in scored if score >= 80][:3]


def describe_windows(limit: int = 12) -> str:
    windows = top_windows()[:limit]
    return "; ".join(f'"{w.title}"' for w in windows) or "(none)"


# --------------------------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.replace("&", "").casefold().split())


def name_score(query: str | None, element: Element) -> int:
    """How well ``element`` matches the name query (0 = not at all, 100 = exact)."""
    if not query:
        return 50
    wanted = _norm(query)
    name = _norm(element.name)
    if not wanted:
        return 50
    if name == wanted:
        return 100
    if element.automation_id and element.automation_id.casefold() == wanted:
        return 95
    if name.startswith(wanted):
        return 90
    if wanted in name:
        return 82
    value = _norm(str(element.state.get("value", "")))
    if value and value == wanted:
        return 70
    if name and len(wanted) >= 3:
        ratio = fuzz.WRatio(wanted, name)
        if ratio >= 85:
            return min(ratio - 20, 74)
    return 0


def search(
    windows: Iterable[TopWindow],
    *,
    name: str | None = None,
    control_type: str | None = None,
    automation_id: str | None = None,
    include_offscreen: bool = False,
    limit: int = 15,
    roots: Iterable[tuple[Any, TopWindow | None]] | None = None,
) -> tuple[list[Element], int]:
    """Search real windows for matching elements. Returns (ranked matches, total scanned)."""
    client = _client()
    condition = client.ControlViewCondition
    if control_type:
        condition = client.CreateAndCondition(
            condition, client.CreatePropertyCondition(P.ControlTypeProperty, normalize_control_type(control_type))
        )
    if automation_id:
        condition = client.CreateAndCondition(
            condition, client.CreatePropertyCondition(P.AutomationIdProperty, automation_id)
        )
    request = _cache_request()
    scopes: list[tuple[Any, TopWindow | None]] = list(roots or [])
    for window in windows:
        try:
            scopes.append((client.ElementFromHandle(window.handle), window))
        except (COMError, OSError) as exc:
            logger.debug("ElementFromHandle(%s) failed: %s", window.handle, exc)
    scored: list[tuple[int, Element]] = []
    scanned = 0
    for root, window in scopes:
        try:
            found = root.FindAllBuildCache(TreeScope.TreeScope_Descendants, condition, request)
        except (COMError, OSError) as exc:
            logger.debug("FindAllBuildCache failed: %s", exc)
            continue
        if not found:
            continue
        for index in range(found.Length):
            scanned += 1
            element = element_from_raw(
                found.GetElement(index),
                cached=True,
                window_handle=window.handle if window else 0,
                window_title=window.title if window else "",
            )
            if not include_offscreen and (element.offscreen or not element.has_area):
                continue
            score = name_score(name, element)
            if score <= 0:
                continue
            scored.append((score, element))
    scored.sort(
        key=lambda pair: (-pair[0], not pair[1].enabled, pair[1].offscreen, pair[1].rect[1], pair[1].rect[0])
    )
    return [element for _, element in scored[:limit]], scanned


def children_named(container: Element, name: str | None, limit: int = 50) -> list[Element]:
    """Descendants of ``container`` matching ``name`` (offscreen included: list items scroll)."""
    matches, _ = search(
        [],
        name=name,
        include_offscreen=True,
        limit=limit,
        roots=[(container.raw, window_info(container.window_handle))],
    )
    return matches


# --------------------------------------------------------------------------------------------
# Stable references
# --------------------------------------------------------------------------------------------


class ElementRegistry:
    """Short, stable references (``e1``, ``e2`` ...) to live elements.

    The same UI element keeps the same reference across searches (keyed by its UIA runtime id).
    """

    def __init__(self, capacity: int = 512) -> None:
        self.capacity = capacity
        self._by_ref: OrderedDict[str, Element] = OrderedDict()
        self._by_runtime_id: dict[tuple, str] = {}
        self._next = 1

    def register(self, element: Element) -> str:
        ref = None
        known = self._by_runtime_id.get(element.runtime_id) if element.runtime_id else None
        if known and known in self._by_ref:
            ref = known
        elif element.ref and element.ref in self._by_ref:
            ref = element.ref  # same logical element found again after its window was rebuilt
        if ref is None:
            ref = f"e{self._next}"
            self._next += 1
        element.ref = ref
        self._by_ref[ref] = element
        self._by_ref.move_to_end(ref)
        if element.runtime_id:
            self._by_runtime_id[element.runtime_id] = ref
        while len(self._by_ref) > self.capacity:
            old_ref, old = self._by_ref.popitem(last=False)
            if old.runtime_id and self._by_runtime_id.get(old.runtime_id) == old_ref:
                del self._by_runtime_id[old.runtime_id]
        return ref

    def get(self, ref: str) -> Element | None:
        return self._by_ref.get(ref.strip().lower())

    def __len__(self) -> int:
        return len(self._by_ref)


def relocate(element: Element) -> Element | None:
    """Find the same logical element again after its window was rebuilt."""
    windows = []
    info = window_info(element.window_handle) if element.window_handle else None
    if info:
        windows = [info]
    elif element.window_title:
        windows = resolve_windows(element.window_title)
    if not windows:
        return None
    candidates, _ = search(
        windows,
        name=element.name or None,
        control_type=element.control_type if element.control_type != "Unknown" else None,
        automation_id=element.automation_id or None,
        include_offscreen=True,
        limit=10,
    )
    exact = [c for c in candidates if c.name == element.name]
    pool = exact or candidates
    if not pool:
        return None
    cx, cy = element.center
    best = min(pool, key=lambda c: math.dist(c.center, (cx, cy)))
    best.ref = element.ref
    return best


def resolve_ref(registry: ElementRegistry, ref: str) -> Element:
    element = registry.get(ref)
    if element is None:
        raise ElementNotFound(
            f"unknown element reference {ref!r}. References come from Find (or Act's candidate "
            "list); call Find again to get fresh ones."
        )
    fresh = refresh(element) or relocate(element)
    if fresh is None:
        raise StaleTarget(
            f"{ref} ({element.label} in \"{element.window_title}\") no longer exists -- the window "
            "was closed or its content changed. Call Find again."
        )
    registry.register(fresh)
    return fresh


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, right - left) * max(0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def element_at(x: int, y: int) -> Element | None:
    try:
        raw = _client().ElementFromPoint(wt.POINT(int(x), int(y)))
    except (COMError, OSError):
        return None
    if not raw:
        return None
    root = root_at(x, y)
    info = window_info(root)
    return element_from_raw(raw, window_handle=root, window_title=info.title if info else "")


def _ancestors(raw: Any, depth: int = 10) -> Iterable[Any]:
    walker = _client().ControlViewWalker
    current = raw
    for _ in range(depth):
        if not current:
            return
        yield current
        try:
            current = walker.GetParentElement(current)
        except (COMError, OSError):
            return


def resolve_snapshot_node(node: Any) -> Element | None:
    """Turn a Snapshot label's node into the live element it refers to *now*.

    ``None`` means the node has no UIA element of its own (word boxes); callers fall back to
    the recorded coordinates. Raises :class:`StaleTarget` when the element is gone.
    """
    if getattr(node, "control_type", "") == "Word":
        return None
    box = node.bounding_box
    node_rect = (box.left, box.top, box.right, box.bottom)
    name = (node.name or "").strip()
    x, y = node.center.x, node.center.y
    try:
        hit = _client().ElementFromPoint(wt.POINT(int(x), int(y)))
    except (COMError, OSError):
        hit = None
    root = root_at(x, y)
    info = window_info(root)
    for raw in _ancestors(hit, 8):
        candidate = element_from_raw(raw, window_handle=root, window_title=info.title if info else "")
        if name and candidate.name == name:
            return candidate
        if not name and _iou(candidate.rect, node_rect) >= 0.7:
            return candidate
    if not name:
        raise StaleTarget(
            f"the unnamed {node.control_type} recorded at ({x},{y}) is no longer there -- the UI "
            "changed since the last Snapshot. Call Snapshot or Find again."
        )
    windows = [w for w in top_windows() if w.title == node.window_name] or resolve_windows(node.window_name)
    candidates, _ = search(windows, name=name, include_offscreen=True, limit=10)
    exact = [c for c in candidates if c.name == name]
    if exact:
        width, height = node_rect[2] - node_rect[0], node_rect[3] - node_rect[1]

        def distance(c: Element) -> float:
            size_penalty = abs((c.rect[2] - c.rect[0]) - width) + abs((c.rect[3] - c.rect[1]) - height)
            return math.dist(c.center, (x, y)) + size_penalty

        return min(exact, key=distance)
    raise StaleTarget(
        f'{node.control_type} "{name}" from the last Snapshot is no longer on screen (window '
        f'"{node.window_name}"). Call Snapshot or Find again.'
    )


# --------------------------------------------------------------------------------------------
# Pointer preparation (visibility, hit-testing, occlusion)
# --------------------------------------------------------------------------------------------


def hits(element: Element, x: int, y: int) -> bool:
    """True if a click at (x, y) would land on ``element`` (or one of its children)."""
    client = _client()
    try:
        hit = client.ElementFromPoint(wt.POINT(int(x), int(y)))
    except (COMError, OSError):
        return False
    for raw in _ancestors(hit, 12):
        try:
            if client.CompareElements(raw, element.raw):
                return True
        except (COMError, OSError):
            return False
    return False


def _candidate_points(element: Element) -> list[tuple[int, int]]:
    left, top, right, bottom = element.rect
    points = [element.center]
    try:
        point, found = element.raw.GetClickablePoint()
        if found and left <= point.x < right and top <= point.y < bottom:
            points.append((int(point.x), int(point.y)))
    except (COMError, OSError, ValueError):
        pass
    width, height = right - left, bottom - top
    for fx, fy in ((0.3, 0.5), (0.7, 0.5), (0.5, 0.3), (0.5, 0.7), (0.15, 0.5)):
        points.append((left + int(width * fx), top + int(height * fy)))
    unique: list[tuple[int, int]] = []
    for point in points:
        if point not in unique:
            unique.append(point)
    return unique


def scroll_into_view(element: Element) -> bool:
    pattern = get_pattern(element.raw, PatternId.ScrollItemPattern)
    if pattern is None:
        return False
    try:
        finished, _ = call_with_timeout(pattern.ScrollIntoView, timeout=3.0)
        return finished
    except (COMError, OSError):
        return False


def prepare_for_pointer(desktop: Any, element: Element) -> tuple[int, int, Element, list[str]]:
    """Make ``element`` clickable and return a point that verifiably lands on it.

    Restores a minimised window, scrolls the element into view, brings its window to the
    front if something covers it, and hit-tests candidate points. Raises instead of clicking
    blind.
    """
    from windows_mcp.desktop import flash_overlay

    flash_overlay.cancel_active_flash()
    notes: list[str] = []
    if not element.enabled:
        raise NotEnabledError(f"{element.label} is disabled; it cannot be clicked right now.")
    root = element.window_handle or root_at(*element.center)
    if root and win32gui.IsWindow(root) and win32gui.IsIconic(root):
        win32gui.ShowWindow(root, win32con.SW_RESTORE)
        time.sleep(0.25)
        notes.append("restored the minimised window")
        element = refresh(element) or element
    if element.offscreen or not element.has_area:
        if scroll_into_view(element):
            time.sleep(0.15)
            notes.append("scrolled it into view")
            element = refresh(element) or element
    for attempt in range(2):
        for x, y in _candidate_points(element):
            if hits(element, x, y):
                return x, y, element, notes
        if attempt == 0 and root:
            desktop.bring_window_to_top(root)
            time.sleep(0.15)
            notes.append("brought its window to the front")
            element = refresh(element) or element
    cover = element_at(*element.center)
    covered_by = f'{cover.label} in "{cover.window_title}"' if cover else "another window"
    raise OccludedError(
        f"{element.label} is not reachable with the pointer: the point {element.center} is covered by "
        f"{covered_by}. Close or move that window, or use a pattern action (invoke/toggle/select)."
    )


# --------------------------------------------------------------------------------------------
# Keyboard safety interlock
# --------------------------------------------------------------------------------------------


def _owned_by(hwnd: int, owner: int) -> bool:
    current = hwnd
    for _ in range(8):
        try:
            current = win32gui.GetWindow(current, win32con.GW_OWNER)
        except win32gui.error:
            return False
        if not current:
            return False
        if root_of(current) == owner:
            return True
    return False


def ensure_foreground(desktop: Any, hwnd: int) -> None:
    """Bring ``hwnd``'s top-level window to the foreground or raise -- never type blind."""
    target = root_of(hwnd) or hwnd
    if not target or not win32gui.IsWindow(target):
        raise FocusLostError("the target window no longer exists, so keyboard input was not sent.")

    def ok() -> bool:
        foreground = foreground_root()
        return foreground == target or (foreground != 0 and _owned_by(foreground, target))

    if ok():
        return
    if win32gui.IsIconic(target):
        win32gui.ShowWindow(target, win32con.SW_RESTORE)
    desktop.bring_window_to_top(target)
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        if ok():
            return
        time.sleep(0.03)
    current = window_info(foreground_root())
    wanted = window_info(target)
    raise FocusLostError(
        f'keyboard input was NOT sent: "{wanted.title if wanted else target}" could not be brought to '
        f'the foreground (the foreground window is "{current.title if current else "?"}"). '
        "Something else took focus -- look at the screen again before retrying."
    )


# --------------------------------------------------------------------------------------------
# Observation of side effects
# --------------------------------------------------------------------------------------------


@dataclass
class Observation:
    foreground: TopWindow | None
    windows: dict[int, str]
    focus: str | None


def focused_element() -> Element | None:
    try:
        raw = _client().GetFocusedElement()
    except (COMError, OSError):
        return None
    if not raw:
        return None
    return element_from_raw(raw)


def observe(include_focus: bool = True) -> Observation:
    focus = None
    if include_focus:
        element = focused_element()
        focus = element.label if element else None
    return Observation(
        foreground=window_info(foreground_root()),
        windows={w.handle: w.title for w in top_windows()},
        focus=focus,
    )


def describe_changes(before: Observation, after: Observation) -> list[str]:
    changes: list[str] = []
    for handle, title in after.windows.items():
        if handle not in before.windows:
            changes.append(f'window opened: "{title}"')
        elif before.windows[handle] != title:
            changes.append(f'window title changed: "{before.windows[handle]}" -> "{title}"')
    for handle, title in before.windows.items():
        if handle not in after.windows:
            changes.append(f'window closed: "{title}"')
    before_fg = before.foreground.handle if before.foreground else 0
    after_fg = after.foreground.handle if after.foreground else 0
    if before_fg != after_fg and after.foreground:
        changes.append(f'foreground is now "{after.foreground.title}"')
    if before.focus != after.focus and after.focus:
        changes.append(f"keyboard focus moved to {after.focus}")
    return changes


def wait_for_changes(before: Observation, timeout: float = 0.5, include_focus: bool = True) -> list[str]:
    """Poll briefly for visible consequences of an action; returns what changed."""
    deadline = time.monotonic() + max(0.0, timeout)
    changes: list[str] = []
    while True:
        time.sleep(0.06)
        after = observe(include_focus)
        changes = describe_changes(before, after)
        if changes:
            time.sleep(0.1)  # let a burst of changes (dialog + focus) finish
            changes = describe_changes(before, observe(include_focus))
            break
        if time.monotonic() >= deadline:
            break
    return changes


def effects_line(changes: list[str]) -> str:
    if not changes:
        return "Effects: no window, title or focus change detected (the app may update without them; check with Screenshot/Find if it matters)."
    return "Effects: " + "; ".join(changes) + "."


# --------------------------------------------------------------------------------------------
# Scroll position (verification for wheel scrolling)
# --------------------------------------------------------------------------------------------


def scroll_state_at(x: int, y: int) -> tuple[str, float | None, float | None] | None:
    try:
        hit = _client().ElementFromPoint(wt.POINT(int(x), int(y)))
    except (COMError, OSError):
        return None
    for raw in _ancestors(hit, 12):
        pattern = get_pattern(raw, PatternId.ScrollPattern)
        if pattern is None:
            continue
        try:
            vertical = float(pattern.CurrentVerticalScrollPercent) if pattern.CurrentVerticallyScrollable else None
            horizontal = (
                float(pattern.CurrentHorizontalScrollPercent) if pattern.CurrentHorizontallyScrollable else None
            )
        except (COMError, OSError):
            continue
        if vertical is None and horizontal is None:
            continue
        return element_from_raw(raw).label, vertical, horizontal
    return None


# --------------------------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------------------------
#
# Strategy ("via"):
#   auto  -- real mouse/keyboard first (exactly what a person does, so the application runs its
#            own handlers: CBN_SELCHANGE, BN_CLICKED, validation, JS listeners ...); UIA patterns
#            only as a fallback when the pointer path is impossible or did not work.
#   input -- real mouse/keyboard only.
#   uia   -- UIA patterns only: works on covered/background windows and never moves the user's
#            pointer; for classic Win32 lists/combos/trackbars the change notification a real
#            user action would have produced is sent to the owning window as well.

ACTIONS = (
    "click",
    "double_click",
    "right_click",
    "hover",
    "invoke",
    "toggle",
    "select",
    "expand",
    "collapse",
    "set_value",
    "type",
    "set_range",
    "focus",
    "scroll_into_view",
)
VIA = ("auto", "input", "uia")

_BROWSER_FRAMEWORKS = {"chrome", "gecko"}


@dataclass
class ActOutcome:
    method: str
    verified: bool | None
    detail: str
    notes: list[str] = field(default_factory=list)
    element: Element | None = None


def _pointer(desktop: Any, element: Element, button: str, count: int) -> ActOutcome:
    x, y, element, notes = prepare_for_pointer(desktop, element)
    native_input.click(x, y, button=button, count=count, glide=0.12 if count == 0 else 0.04)
    kind = {0: "moved the pointer onto", 1: f"{button} click on", 2: f"{button} double click on"}[count]
    return ActOutcome(f"real {kind} it at ({x},{y})", None, "", notes, element)


def _call_pattern(element: Element, pattern_id: int, method: str, *args: Any, timeout: float = 5.0) -> bool:
    """Call a pattern method; ``False`` if the element does not support the pattern."""
    pattern = get_pattern(element.raw, pattern_id)
    if pattern is None:
        return False
    call_with_timeout(lambda: getattr(pattern, method)(*args), timeout=timeout)
    return True


def _poll_state(element: Element, key: str, previous: Any, timeout: float = 1.0) -> tuple[Element, Any]:
    """Re-read ``element`` until ``state[key]`` differs from ``previous`` (providers update async)."""
    deadline = time.monotonic() + timeout
    fresh = element
    while True:
        fresh = refresh(element) or fresh
        current = fresh.state.get(key)
        if current != previous or time.monotonic() >= deadline:
            return fresh, current
        time.sleep(0.05)


def _parse_toggle(value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lower()
    if text in {"on", "true", "1", "yes", "checked", "check"}:
        return "on"
    if text in {"off", "false", "0", "no", "unchecked", "uncheck"}:
        return "off"
    raise ValueError("toggle value must be on/off (or true/false); omit it to flip the state")


def _same_text(a: Any, b: Any) -> bool:
    return str(a).replace("\r\n", "\n").rstrip() == str(b).replace("\r\n", "\n").rstrip()


# ---- classic Win32 notifications (only used after a UIA-pattern change) ----------------------

WM_COMMAND, WM_HSCROLL, WM_VSCROLL = 0x0111, 0x0114, 0x0115
TB_THUMBPOSITION, TB_ENDTRACK, TBS_VERT = 4, 8, 0x0002
CBN_SELCHANGE = LBN_SELCHANGE = 1


def _native_handle(element: Element) -> int:
    value = _read(element.raw, P.NativeWindowHandleProperty, cached=False)
    return int(value or 0)


def _notify_win32_selection(container: Element) -> str | None:
    hwnd = _native_handle(container)
    if not hwnd:
        return None
    try:
        class_name = win32gui.GetClassName(hwnd).lower()
        if "combobox" not in class_name and "listbox" not in class_name:
            return None
        parent = win32gui.GetParent(hwnd)
        control_id = win32gui.GetDlgCtrlID(hwnd)
        code = CBN_SELCHANGE if "combobox" in class_name else LBN_SELCHANGE
        win32gui.PostMessage(parent, WM_COMMAND, (code << 16) | (control_id & 0xFFFF), hwnd)
    except win32gui.error:
        return None
    return "sent the selection-change notification a real user selection produces"


def _notify_win32_trackbar(element: Element, position: float) -> str | None:
    hwnd = _native_handle(element)
    if not hwnd:
        return None
    try:
        if "trackbar" not in win32gui.GetClassName(hwnd).lower():
            return None
        parent = win32gui.GetParent(hwnd)
        vertical = bool(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & TBS_VERT)
        message = WM_VSCROLL if vertical else WM_HSCROLL
        win32gui.PostMessage(parent, message, (int(position) << 16) | TB_THUMBPOSITION, hwnd)
        win32gui.PostMessage(parent, message, TB_ENDTRACK, hwnd)
    except win32gui.error:
        return None
    return "sent the scroll notifications a real thumb drag produces"


# ---- keyboard text ---------------------------------------------------------------------------


def keyboard_replace(desktop: Any, element: Element, text: str, *, clear: bool = True) -> list[str]:
    """Focus ``element`` with a real click and type ``text`` (optionally replacing its content)."""
    x, y, element, notes = prepare_for_pointer(desktop, element)
    native_input.click(x, y)
    window = element.window_handle or root_at(x, y)
    ensure_foreground(desktop, window)
    if clear:
        native_input.press_keys("ctrl+a")
        native_input.press_keys("backspace")
    ensure_foreground(desktop, window)
    native_input.type_text(text)
    return notes


# ---- individual actions ------------------------------------------------------------------------


def _click_like(desktop: Any, element: Element, action: str, via: str) -> ActOutcome:
    button = "right" if action == "right_click" else "left"
    count = {"click": 1, "double_click": 2, "right_click": 1, "hover": 0}[action]
    if via != "uia":
        try:
            return _pointer(desktop, element, button, count)
        except OccludedError as exc:
            if via == "input" or action != "click":
                raise
            blocked = str(exc)
    else:
        if action != "click":
            raise ElementError(f"{action} needs real input; use via='auto' or 'input'.")
        blocked = ""
    for pattern_id, method in (
        (PatternId.InvokePattern, "Invoke"),
        (PatternId.SelectionItemPattern, "Select"),
        (PatternId.TogglePattern, "Toggle"),
    ):
        if _call_pattern(element, pattern_id, method):
            notes = [f"pointer path blocked ({blocked}); used UIA {method} instead"] if blocked else []
            return ActOutcome(f"UIA {method}", None, "", notes, element)
    raise ElementError(f"{element.label} supports no UIA Invoke/Select/Toggle, and it cannot be clicked: {blocked}")


def _toggle(desktop: Any, element: Element, value: str | None, via: str) -> ActOutcome:
    desired = _parse_toggle(value)
    before = element.state.get("toggle")
    if desired is not None and before == desired:
        return ActOutcome("no action needed", True, f"already {desired}", [], element)
    notes: list[str] = []
    method = ""
    current, fresh = before, element
    if via != "uia":
        try:
            for _ in range(3):  # tri-state boxes may need two clicks
                outcome = _pointer(desktop, fresh, "left", 1)
                notes += outcome.notes
                method = "real left click"
                fresh, current = _poll_state(outcome.element or fresh, "toggle", current)
                if desired is None or current == desired or current is None:
                    break
        except OccludedError as exc:
            if via == "input":
                raise
            notes.append(f"pointer path blocked ({exc})")
    done = current == desired if desired is not None else current != before
    if not done and via != "input" and get_pattern(element.raw, PatternId.TogglePattern) is not None:
        for _ in range(3):
            _call_pattern(fresh, PatternId.TogglePattern, "Toggle")
            method = "UIA Toggle" if not method else method + " + UIA Toggle"
            fresh, current = _poll_state(fresh, "toggle", current)
            if desired is None or current == desired:
                break
    if current is None:
        return ActOutcome(method or "nothing", None, "the control does not report a toggle state", notes, fresh)
    verified = (current == desired) if desired is not None else (current != before)
    return ActOutcome(method or "nothing", verified, f"state {before} -> {current}", notes, fresh)


_ITEM_TYPES = {"ListItem", "TreeItem", "TabItem", "DataItem", "MenuItem", "RadioButton"}


def _find_item(container: Element, value: str) -> Element | None:
    items = children_named(container, value, limit=30)
    items = [i for i in items if i.control_type in _ITEM_TYPES] or items
    exact = [i for i in items if _norm(i.name) == _norm(value)]
    pool = exact or items
    return pool[0] if pool else None


def _select_item(desktop: Any, container: Element, value: str, via: str) -> ActOutcome:
    notes: list[str] = []
    method = ""
    is_combo = container.control_type == "ComboBox"
    opened = False
    if is_combo and container.state.get("expanded") != "expanded":
        if via != "uia":
            try:
                x, y, container, extra = prepare_for_pointer(desktop, container)
                native_input.click(x, y)
                notes += extra
                opened = True
            except OccludedError as exc:
                if via == "input":
                    raise
                notes.append(f"pointer path blocked ({exc})")
        container, state = _poll_state(container, "expanded", container.state.get("expanded"), timeout=0.6)
        if state != "expanded" and via != "input":
            opened = _call_pattern(container, PatternId.ExpandCollapsePattern, "Expand", timeout=3.0) or opened
            time.sleep(0.15)
    item = _find_item(container, value)
    if item is None:
        available = [
            c.name for c in children_named(container, None, limit=60) if c.name and c.control_type in _ITEM_TYPES
        ]
        if opened:
            _call_pattern(container, PatternId.ExpandCollapsePattern, "Collapse", timeout=3.0)
        raise ElementNotFound(
            f"{container.label} has no item named {value!r}. Items: {', '.join(available[:30]) or '(none visible)'}"
        )
    selected = False
    if via != "uia":
        try:
            if item.offscreen or not item.has_area:
                scroll_into_view(item)
                item = refresh(item) or item
            outcome = _pointer(desktop, item, "left", 1)
            notes += outcome.notes
            method = f'real click on item "{item.name}"'
            selected = True
        except (OccludedError, NotEnabledError) as exc:
            if via == "input":
                raise
            notes.append(f"could not click the item ({exc})")
    if not selected:
        scroll_into_view(item)
        if _call_pattern(item, PatternId.SelectionItemPattern, "Select"):
            method = f'UIA Select of item "{item.name}"'
            note = _notify_win32_selection(container)
            if note:
                notes.append(note)
        else:
            raise ElementError(f'item "{item.name}" can be neither clicked nor selected through UIA.')
    time.sleep(0.15)
    fresh_container = refresh(container) or container
    if is_combo and fresh_container.state.get("expanded") == "expanded":
        _call_pattern(fresh_container, PatternId.ExpandCollapsePattern, "Collapse", timeout=3.0)
        fresh_container = refresh(container) or fresh_container
    fresh_item = refresh(item)
    shown = fresh_container.state.get("value")
    if shown is not None and is_combo:
        verified = _norm(str(shown)) == _norm(item.name)
        detail = f'{container.control_type} now shows "{_clip(shown, 60)}"'
    else:
        is_selected = bool(fresh_item and fresh_item.state.get("selected"))
        verified = is_selected if fresh_item and "selected" in fresh_item.state else None
        detail = f'item "{item.name}" is {"selected" if is_selected else "NOT selected"}'
    return ActOutcome(method, verified, detail, notes, fresh_container)


def _select_self(desktop: Any, element: Element, via: str) -> ActOutcome:
    outcome: ActOutcome | None = None
    if via != "uia":
        try:
            outcome = _pointer(desktop, element, "left", 1)
        except OccludedError:
            if via == "input":
                raise
    if outcome is None:
        if not _call_pattern(element, PatternId.SelectionItemPattern, "Select"):
            raise ElementError(f"{element.label} cannot be selected through UIA.")
        outcome = ActOutcome("UIA Select", None, "", [], element)
    fresh, state = _poll_state(outcome.element or element, "selected", element.state.get("selected"))
    outcome.element = fresh
    if state is not None:
        outcome.verified = bool(state)
        outcome.detail = "selected" if state else "NOT selected afterwards"
    return outcome


def _expand(desktop: Any, element: Element, action: str, via: str) -> ActOutcome:
    wanted = "expanded" if action == "expand" else "collapsed"
    before = element.state.get("expanded")
    if before == wanted:
        return ActOutcome("no action needed", True, f"already {wanted}", [], element)
    if via == "input" or get_pattern(element.raw, PatternId.ExpandCollapsePattern) is None:
        if via == "uia":
            raise ElementError(f"{element.label} has no UIA ExpandCollapse support.")
        outcome = _pointer(desktop, element, "left", 1)
        fresh, state = _poll_state(outcome.element or element, "expanded", before)
        outcome.element = fresh
        outcome.verified = (state == wanted) if state is not None else None
        outcome.detail = f"now {state}" if state else ""
        return outcome
    _call_pattern(element, PatternId.ExpandCollapsePattern, action.title(), timeout=4.0)
    fresh, state = _poll_state(element, "expanded", before)
    return ActOutcome(f"UIA {action.title()}", state == wanted, f"now {state}", [], fresh)


def _set_text(desktop: Any, element: Element, action: str, value: str, via: str) -> ActOutcome:
    notes: list[str] = []
    replace = action == "set_value"
    has_pattern = get_pattern(element.raw, PatternId.ValuePattern) is not None
    read_only = bool(element.state.get("read_only"))
    browser = element.framework.lower() in _BROWSER_FRAMEWORKS
    # A newline typed into a single-line field is an Enter key press, which can submit a form;
    # for multi-line values prefer setting the value directly when the control allows it.
    multiline_value = "\n" in value or "\r" in value

    def by_pattern() -> str:
        call_with_timeout(lambda: get_pattern(element.raw, PatternId.ValuePattern).SetValue(value))
        return "UIA SetValue"

    def by_keyboard() -> str:
        notes.extend(keyboard_replace(desktop, element, value, clear=replace))
        return "real keyboard typing" + (" (replaced the content)" if replace else "")

    if via == "uia" or (via == "auto" and replace and multiline_value and has_pattern and not read_only and not browser):
        if not replace:
            raise ElementError("action=type appends with the keyboard; use set_value for via='uia'.")
        if not has_pattern or read_only:
            raise ElementError(f"{element.label} does not accept a value through UIA (no writable ValuePattern).")
        method = by_pattern()
    else:
        method = by_keyboard()
    time.sleep(0.05)
    fresh = refresh(element) or element
    current = fresh.state.get("value")
    ok = None
    if not element.password and current is not None:
        ok = _same_text(current, value) if replace else str(value).rstrip() in str(current)
    if (
        ok is False
        and via == "auto"
        and replace
        and method.startswith("real keyboard")
        and has_pattern
        and not read_only
        and not browser
    ):
        notes.append("typing did not produce the exact text; set it through UIA instead")
        method += " + UIA SetValue"
        by_pattern()
        time.sleep(0.05)
        fresh = refresh(element) or fresh
        current = fresh.state.get("value")
        ok = _same_text(current, value) if current is not None else None
    if element.password:
        return ActOutcome(method, None, "password field -- its content cannot be read back", notes, fresh)
    if current is None:
        return ActOutcome(method, None, "the field does not expose its text, so it could not be verified", notes, fresh)
    return ActOutcome(method, ok, f'field now contains "{_clip(current, 80)}"', notes, fresh)


def _set_range(element: Element, value: str | None, via: str) -> ActOutcome:
    if value is None:
        raise ValueError("action=set_range needs a numeric value")
    if via == "input":
        raise ElementError("set_range is done through UIA; drag the thumb with Move(drag=True) for pure input.")
    number = float(value)
    pattern = get_pattern(element.raw, PatternId.RangeValuePattern)
    if pattern is None:
        raise ElementError(f"{element.label} is not a range control (slider/spinner/progress).")
    low, high = float(pattern.CurrentMinimum), float(pattern.CurrentMaximum)
    if not low <= number <= high:
        raise ValueError(f"value {number:g} is outside the allowed range {low:g}..{high:g}")
    call_with_timeout(lambda: pattern.SetValue(number))
    notes = [note] if (note := _notify_win32_trackbar(element, number)) else []
    fresh, current_range = _poll_state(element, "range", element.state.get("range"))
    current = current_range[0] if current_range else None
    ok = current is not None and abs(float(current) - number) <= max(1e-6, (high - low) * 0.005)
    return ActOutcome("UIA RangeValue.SetValue", ok, f"value now {_fmt(current)}", notes, fresh)


def act(desktop: Any, element: Element, action: str, value: str | None = None, via: str = "auto") -> ActOutcome:
    """Perform ``action`` on the (fresh) ``element`` and verify the result where possible."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")
    if via not in VIA:
        raise ValueError(f"via must be one of: {', '.join(VIA)}")

    if action in {"click", "double_click", "right_click", "hover"}:
        return _click_like(desktop, element, action, via)

    if action == "invoke":
        if via != "input" and _call_pattern(element, PatternId.InvokePattern, "Invoke"):
            return ActOutcome("UIA Invoke", None, "", [], element)
        if via == "uia":
            raise ElementError(f"{element.label} has no UIA Invoke support.")
        outcome = _pointer(desktop, element, "left", 1)
        if via == "auto":
            outcome.notes.append("it has no UIA Invoke support, so it was clicked instead")
        return outcome

    if action == "toggle":
        return _toggle(desktop, element, value, via)

    if action == "select":
        return _select_item(desktop, element, value, via) if value else _select_self(desktop, element, via)

    if action in {"expand", "collapse"}:
        return _expand(desktop, element, action, via)

    if action in {"set_value", "type"}:
        if value is None:
            raise ValueError(f"action={action} needs value")
        return _set_text(desktop, element, action, value, via)

    if action == "set_range":
        return _set_range(element, value, via)

    if action == "focus":
        if via == "input":
            outcome = _pointer(desktop, element, "left", 1)
            fresh, focused = _poll_state(outcome.element or element, "focused", False) if not element.focused else (element, True)
        else:
            try:
                element.raw.SetFocus()
            except (COMError, OSError) as exc:
                raise ElementError(f"{element.label} refused keyboard focus ({exc}).") from exc
            outcome = ActOutcome("UIA SetFocus", None, "", [], element)
            fresh = refresh(element) or element
            focused = fresh.focused
        outcome.element, outcome.verified = fresh, bool(focused)
        outcome.detail = "has keyboard focus" if focused else "focus did not move"
        return outcome

    # scroll_into_view
    moved = scroll_into_view(element)
    fresh = refresh(element) or element
    if not moved:
        raise ElementError(f"{element.label} does not support scrolling into view.")
    return ActOutcome("UIA ScrollIntoView", not fresh.offscreen, "on screen" if not fresh.offscreen else "still offscreen", [], fresh)
