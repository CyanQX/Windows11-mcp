"""Input tools — Click, Type, Scroll, Move, Shortcut, Wait, WaitFor."""

import json
import math
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

from mcp.types import ToolAnnotations
from windows_mcp.desktop import elements, native_input
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context


WaitForCondition = Literal[
    "text_exists",
    "active_window",
    "element_exists",
    "element_enabled",
    "focused_element",
]


def _resolve_label(desktop: Any, label: int) -> list[int]:
    """Resolve a UI element label to the coordinates recorded by the last Snapshot."""
    if desktop.desktop_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
    try:
        return list(desktop.get_coordinates_from_label(label))
    except Exception as e:
        raise ValueError(f"Failed to find element with label {label}: {e}")


def _label_point(desktop: Any, label: int) -> tuple[int, int, Any, list[str]]:
    """Live point for a Snapshot label: the element is re-located, scrolled into view and
    hit-tested *now*, so a window that moved since the Snapshot is still hit correctly."""
    if desktop.desktop_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
    element = desktop.locate_label(label)
    if element is None:  # word boxes have no element of their own
        x, y = _resolve_label(desktop, label)
        return x, y, None, ["word box: used the coordinates recorded by Snapshot"]
    x, y, element, notes = elements.prepare_for_pointer(desktop, element)
    return x, y, element, notes


def _describe_point(x: int, y: int) -> str:
    element = elements.element_at(x, y)
    if element is None:
        return "an unidentified element"
    return f'{element.label} in "{element.window_title}"' if element.window_title else element.label


def _notes(notes: list[str]) -> str:
    return f"\nNotes: {'; '.join(notes)}." if notes else ""


def _clip(text: str, limit: int = 60) -> str:
    flat = text.replace("\r\n", "\\n").replace("\n", "\\n")
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _as_bool(value: bool | str, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be true or false")


def _validate_finite_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _as_loc(value: list | str | None) -> list | None:
    """Coerce a JSON-stringified list back to a list.

    Claude Desktop strips anyOf schemas and the model serializes lists as
    strings (e.g. '[100, 200]'). Parsing here keeps the tools working.
    """
    if value is None or isinstance(value, list):
        return value
    return json.loads(value)


def _as_point(value: object, name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} must be a list of exactly 2 integers [x, y]")
    parsed = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"{name} must contain integers, not booleans")
        if isinstance(item, int):
            parsed.append(item)
            continue
        if isinstance(item, str):
            stripped = item.strip()
            if stripped and stripped.lstrip("+-").isdigit():
                parsed.append(int(stripped))
                continue
        raise ValueError(f"{name} must contain exactly 2 integers")
    return parsed


def _text_matches(value: object | None, expected: str | None) -> bool:
    if expected is None:
        return True
    if value is None:
        return False
    return expected.casefold() in str(value).casefold()


def _metadata_text_matches(metadata: dict[str, object], expected: str | None) -> bool:
    return any(_text_matches(value, expected) for value in metadata.values())


def _iter_nodes(desktop_state: Any) -> Iterator[Any]:
    tree_state = getattr(desktop_state, "tree_state", None)
    if tree_state is None:
        return
    yield from getattr(tree_state, "interactive_nodes", [])
    yield from getattr(tree_state, "scrollable_nodes", [])


def _iter_text_sources(desktop_state: Any) -> Iterator[object]:
    active_window = getattr(desktop_state, "active_window", None)
    if active_window is not None:
        yield active_window.name

    for window in getattr(desktop_state, "windows", []):
        yield window.name

    tree_state = getattr(desktop_state, "tree_state", None)
    if tree_state is None:
        return

    for node in _iter_nodes(desktop_state):
        yield node.name
        yield node.control_type
        yield node.window_name
        for value in getattr(node, "metadata", {}).values():
            yield value

    for node in getattr(tree_state, "dom_informative_nodes", []):
        yield getattr(node, "text", "")


def _node_matches(node: Any, text: str | None, window_name: str | None) -> bool:
    metadata: dict[str, object] = getattr(node, "metadata", {})
    return (
        _text_matches(getattr(node, "name", ""), text)
        or _text_matches(getattr(node, "control_type", ""), text)
        or _metadata_text_matches(metadata, text)
    ) and _text_matches(getattr(node, "window_name", ""), window_name)


def _matches_wait_condition(
    desktop_state: Any,
    condition: WaitForCondition,
    text: str | None,
    window_name: str | None,
) -> tuple[bool, str]:
    if condition == "text_exists":
        for source in _iter_text_sources(desktop_state):
            if _text_matches(source, text):
                return True, f"text {text!r} appeared"
        return False, f"text {text!r} was absent"

    if condition == "active_window":
        expected = window_name or text
        active_window = getattr(desktop_state, "active_window", None)
        active_name = active_window.name if active_window else ""
        if _text_matches(active_name, expected):
            return True, f"active window matched {active_name!r}"
        return False, f"active window was {active_name!r}"

    if condition in {"element_exists", "element_enabled"}:
        for node in _iter_nodes(desktop_state):
            if _node_matches(node, text, window_name):
                return True, f"element matched {getattr(node, 'name', '')!r}"
        return False, "matching element was absent"

    if condition == "focused_element":
        for node in _iter_nodes(desktop_state):
            metadata = getattr(node, "metadata", {})
            if metadata.get("has_focused") and _node_matches(node, text, window_name):
                return True, f"focused element matched {getattr(node, 'name', '')!r}"
        return False, "matching focused element was absent"

    raise ValueError(f"Unsupported WaitFor condition: {condition}")


def _validate_wait_for_args(
    condition: str,
    text: str | None,
    window_name: str | None,
    timeout: float,
    interval: float,
) -> WaitForCondition:
    _validate_finite_number(timeout, "timeout")
    _validate_finite_number(interval, "interval")

    normalized = condition.strip().lower().replace("-", "_")
    aliases = {
        "text": "text_exists",
        "window": "active_window",
        "element": "element_exists",
        "enabled": "element_enabled",
        "focused": "focused_element",
    }
    normalized = aliases.get(normalized, normalized)
    valid_conditions = {
        "text_exists",
        "active_window",
        "element_exists",
        "element_enabled",
        "focused_element",
    }
    if normalized not in valid_conditions:
        raise ValueError(
            "condition must be one of: text_exists, active_window, element_exists, "
            "element_enabled, focused_element"
        )

    if timeout <= 0 or timeout > 120:
        raise ValueError("timeout must be greater than 0 and at most 120 seconds")
    if interval <= 0 or interval > 5:
        raise ValueError("interval must be greater than 0 and at most 5 seconds")

    if normalized == "text_exists" and not text:
        raise ValueError("text is required when condition is text_exists")
    if normalized == "active_window" and not (text or window_name):
        raise ValueError("text or window_name is required when condition is active_window")
    if normalized in {"element_exists", "element_enabled"} and not (text or window_name):
        raise ValueError(
            "text or window_name is required when condition is element_exists or element_enabled"
        )

    return normalized


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    @mcp.tool(
        name="Click",
        description=(
            "Real mouse click on the desktop at [x, y] (physical screen pixels) or on a Snapshot "
            "label. With label=, the element is re-located live, scrolled into view, its window "
            "brought forward and the exact point hit-tested before clicking, so moved windows are "
            "still hit correctly. button: left / right (context menu) / middle. clicks: 0=hover "
            "only, 1=single, 2=double, 3=triple. Reports which element was under the pointer and "
            "the visible effects (windows opened/closed, focus change). Prefer Act for named controls."
        ),
        annotations=ToolAnnotations(
            title="Click",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Click-Tool")
    def click_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if isinstance(clicks, bool) or clicks not in (0, 1, 2, 3):
            raise ValueError("clicks must be 0 (hover), 1, 2 or 3")
        notes: list[str] = []
        if label is not None:
            x, y, target, notes = _label_point(desktop, label)
            under = (
                f'{target.label} in "{target.window_title}"' if target is not None else _describe_point(x, y)
            )
        else:
            if len(loc) != 2:
                raise ValueError("Location must be a list of exactly 2 integers [x, y]")
            x, y = _as_point(loc, "loc")
            under = _describe_point(x, y)
        before = elements.observe()
        desktop.click(loc=[x, y], button=button, clicks=clicks)
        changes = elements.wait_for_changes(before, timeout=0.5 if clicks else 0.15)
        kind = {0: "Hover", 1: "Single", 2: "Double", 3: "Triple"}[clicks]
        verb = "moved the pointer" if clicks == 0 else f"{button} clicked"
        return (
            f"{kind} {verb} at ({x},{y}) on {under}.{_notes(notes)}\n{elements.effects_line(changes)}"
        )

    @mcp.tool(
        name="Type",
        description=(
            "Type text into a real field at [x, y] or a Snapshot label: clicks it, confirms its window "
            "really holds the keyboard (otherwise nothing is typed), then sends genuine Unicode key "
            "events -- exact for any language/emoji and not intercepted by Chinese/Japanese IMEs. "
            "clear=True replaces the existing text, False appends. press_enter=True submits. "
            "caret_position: 'start' (Home), 'end' (End) or 'idle'. method: 'auto' (real keys; paste "
            "for >2000 chars), 'keys', 'paste' (clipboard, previous text restored afterwards), 'value' "
            "(set through UIA ValuePattern without typing; needs label and clear=True). Reads the "
            "field back afterwards and reports whether the text is really there."
        ),
        annotations=ToolAnnotations(
            title="Type",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Type-Tool")
    def type_tool(
        text: str,
        loc: list[int] | str | None = None,
        label: int | None = None,
        clear: bool | str = False,
        caret_position: Literal["start", "idle", "end"] = "idle",
        press_enter: bool | str = False,
        method: Literal["auto", "keys", "paste", "value"] = "auto",
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        clear_flag = _as_bool(clear, "clear")
        enter_flag = _as_bool(press_enter, "press_enter")
        notes: list[str] = []
        element = None
        if label is not None:
            if desktop.desktop_state is None:
                raise ValueError("Desktop state is empty. Please call Snapshot first.")
            element = desktop.locate_label(label)
        if method == "value":
            if element is None or not clear_flag:
                raise ValueError("method='value' needs a label (of a field with UIA ValuePattern) and clear=True")
            # Explicitly requested: set the value through UI Automation, then read it back.
            outcome = elements.act(desktop, element, "set_value", text, via="uia")
            final = outcome.element or element
            before = elements.observe()
            if enter_flag:
                elements.ensure_foreground(desktop, final.window_handle or elements.root_at(*final.center))
                desktop.shortcut("enter")
            changes = elements.wait_for_changes(before, timeout=0.5 if enter_flag else 0.0)
            status = (
                f"Verified: {outcome.detail}." if outcome.verified
                else f"NOT verified: {outcome.detail}." if outcome.verified is False
                else f"Result: {outcome.detail}."
            )
            return (
                f'Set {final.label} in "{final.window_title}" to "{_clip(text)}" via {outcome.method}'
                f"{' and pressed Enter' if enter_flag else ''}.{_notes(outcome.notes)}\n{status}\n"
                f"{elements.effects_line(changes)}"
            )
        if element is not None:
            x, y, element, notes = elements.prepare_for_pointer(desktop, element)
        elif label is not None:
            x, y = _resolve_label(desktop, label)
        else:
            if len(loc) != 2:
                raise ValueError("Location must be a list of exactly 2 integers [x, y]")
            x, y = _as_point(loc, "loc")
        under = (
            f'{element.label} in "{element.window_title}"' if element is not None else _describe_point(x, y)
        )
        before = elements.observe(include_focus=False)
        used = desktop.type(
            loc=[x, y],
            text=text,
            caret_position=caret_position,
            clear=clear_flag,
            press_enter=enter_flag,
            method=method,
        )
        changes = elements.wait_for_changes(before, timeout=0.5 if enter_flag else 0.0, include_focus=False)
        focused = elements.focused_element()
        if enter_flag:
            check = "Enter was pressed, so the field may have been submitted/cleared; not read back."
        elif focused is None or "value" not in focused.state:
            check = "The focused control does not expose its text, so the result could not be read back."
        elif text.replace("\r\n", "\n").rstrip() in str(focused.state["value"]).replace("\r\n", "\n"):
            check = f'Verified: {focused.label} now contains the typed text.'
        else:
            check = (
                f'NOT verified: {focused.label} contains "{_clip(str(focused.state["value"]), 80)}" -- '
                "look at the field before retrying."
            )
        return (
            f'Typed "{_clip(text)}" ({len(text)} chars, via {used or "keys"}) at ({x},{y}) into {under}'
            f"{' and pressed Enter' if enter_flag else ''}.{_notes(notes)}\n{check}\n"
            f"{elements.effects_line(changes)}"
        )

    @mcp.tool(
        name="Scroll",
        description="Real mouse-wheel scrolling at coordinates [x, y], a Snapshot label, or the current pointer position if loc=None. Type: vertical (default) or horizontal (tilt wheel). Direction: up/down for vertical, left/right for horizontal. wheel_times controls amount (1 notch ≈ 3 lines). Reports the scroll position of the container under the pointer before/after, so you know whether it actually moved or is already at the end.",
        annotations=ToolAnnotations(
            title="Scroll",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Scroll-Tool")
    def scroll_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        if isinstance(wheel_times, bool) or not isinstance(wheel_times, int) or not 1 <= wheel_times <= 50:
            raise ValueError("wheel_times must be an integer between 1 and 50")
        if label is not None:
            node_point = _label_point(desktop, label)
            loc = [node_point[0], node_point[1]]
        if loc and len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        point = tuple(_as_point(loc, "loc")) if loc else native_input.cursor_position()
        before = elements.scroll_state_at(*point)
        response = desktop.scroll(list(point) if loc else None, type, direction, wheel_times)
        if response:
            return response
        text = f"Scrolled {type} {direction} by {wheel_times} wheel notch(es) at ({point[0]},{point[1]})."
        after = elements.scroll_state_at(*point)
        if before and after and before[0] == after[0]:
            axis = 1 if type == "vertical" else 2
            old, new = before[axis], after[axis]
            if old is not None and new is not None:
                if abs(new - old) > 0.01:
                    text += f" {after[0]} scrolled {old:.1f}% -> {new:.1f}%."
                else:
                    text += f" {after[0]} did not move (still at {new:.1f}% -- probably at the end)."
        elif after is None:
            text += " (No UIA scroll container under the pointer; position change not measurable.)"
        return text

    @mcp.tool(
        name="Move",
        description=(
            "Moves mouse cursor to coordinates [x, y] or passing a UI element's label/id. "
            "Set drag=True to perform a drag-and-drop operation from the current mouse position "
            "to the target coordinates, or provide from_loc=[x, y] to make the drag explicit-start "
            "and atomic in one tool call. Optional duration controls bounded intermediate movement. "
            "Default (drag=False) is a simple cursor move (hover). "
            "Provide either loc or label."
        ),
        annotations=ToolAnnotations(
            title="Move",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Move-Tool")
    def move_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        drag: bool | str = False,
        from_loc: list[int] | str | None = None,
        duration: float | int | str | None = None,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        from_loc = _as_loc(from_loc)
        drag = _as_bool(drag, "drag")
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            node_point = _label_point(desktop, label)
            loc = [node_point[0], node_point[1]]
        if not isinstance(loc, list) or len(loc) != 2:
            raise ValueError("loc must be a list of exactly 2 integers [x, y]")
        if from_loc is not None and (not isinstance(from_loc, list) or len(from_loc) != 2):
            raise ValueError("from_loc must be a list of exactly 2 integers [x, y]")
        has_drag_only_options = any(
            value is not None
            for value in (
                from_loc,
                duration,
            )
        )
        if has_drag_only_options and not drag:
            raise ValueError("from_loc and duration require drag=True")
        if drag:
            loc = _as_point(loc, "loc")
            if from_loc is not None:
                from_loc = _as_point(from_loc, "from_loc")
        x, y = loc[0], loc[1]
        if drag:
            result = desktop.drag(
                loc,
                from_loc=from_loc,
                duration=duration,
            )
            start_x, start_y = result["start"]
            effective_duration = result["duration"]
            if effective_duration is None:
                return f"Dragged from ({start_x},{start_y}) to ({x},{y})."
            return (
                f"Dragged from ({start_x},{start_y}) to ({x},{y}) "
                f"over {effective_duration:.3f} seconds."
            )
        else:
            desktop.move(loc)
            return f"Moved the mouse pointer to ({x},{y})."

    @mcp.tool(
        name="Shortcut",
        description='Presses real keyboard shortcuts: keys joined by +, several chords separated by spaces. Examples: "ctrl+c", "ctrl+v", "alt+tab", "win+r", "win", "ctrl+shift+esc", "f5", "enter", "ctrl+k ctrl+s" (a two-step chord), "ctrl++" (ctrl and the + key). Each chord is sent atomically, so modifiers can never stay stuck. repeat presses it N times (e.g. "down" x5). window (title / process / handle) first brings that window to the foreground and refuses to send keys if it cannot -- use it to avoid shortcuts landing in the wrong app. Reports visible effects (windows opened/closed, focus).',
        annotations=ToolAnnotations(
            title="Shortcut",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Shortcut-Tool")
    def shortcut_tool(shortcut: str, repeat: int = 1, window: str | None = None, ctx: Context = None):
        desktop = get_desktop()
        if isinstance(repeat, bool) or not isinstance(repeat, int) or not 1 <= repeat <= 100:
            raise ValueError("repeat must be an integer between 1 and 100")
        native_input.parse_sequence(shortcut)  # validate before touching anything
        target = ""
        if window:
            matches = elements.resolve_windows(window)
            if not matches:
                raise ValueError(f"No open window matches {window!r}. Open windows: {elements.describe_windows()}")
            elements.ensure_foreground(desktop, matches[0].handle)
            target = f' in "{matches[0].title}"'
        before = elements.observe()
        if repeat == 1:
            desktop.shortcut(shortcut)
        else:
            desktop.shortcut(shortcut, repeat=repeat)
        changes = elements.wait_for_changes(before, timeout=0.5)
        times = f" x{repeat}" if repeat > 1 else ""
        return f"Pressed {shortcut}{times}{target}.\n{elements.effects_line(changes)}"

    @mcp.tool(
        name="Wait",
        description="Pauses execution for specified duration in seconds. Use when waiting for: applications to launch/load, UI animations to complete, page content to render, dialogs to appear, or between rapid actions. Helps ensure UI is ready before next interaction.",
        annotations=ToolAnnotations(
            title="Wait",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Wait-Tool")
    def wait_tool(duration: int, ctx: Context = None) -> str:
        time.sleep(duration)
        return f"Waited for {duration} seconds."

    @mcp.tool(
        name="WaitFor",
        description=(
            "Waits until a UI condition is satisfied, polling the Windows accessibility tree "
            "inside the tool to avoid repeated Snapshot calls. Conditions: text_exists, "
            "active_window, element_exists, element_enabled, focused_element. Provide text "
            "and/or window_name depending on the condition. Set use_dom=True for browser DOM text."
        ),
        annotations=ToolAnnotations(
            title="WaitFor",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "WaitFor-Tool")
    def wait_for_tool(
        condition: str,
        text: str | None = None,
        window_name: str | None = None,
        timeout: float = 10.0,
        interval: float = 0.25,
        use_dom: bool | str = False,
        ctx: Context = None,
    ) -> str:
        normalized = _validate_wait_for_args(
            condition=condition,
            text=text,
            window_name=window_name,
            timeout=timeout,
            interval=interval,
        )
        desktop = get_desktop()
        use_dom_bool = _as_bool(use_dom, "use_dom")
        started_at = time.monotonic()
        deadline = started_at + timeout
        attempts = 0
        last_detail = "condition was not evaluated"

        while True:
            attempts += 1
            desktop_state = desktop.get_state(
                use_vision=False,
                use_dom=use_dom_bool,
                use_ui_tree=True,
                use_annotation=False,
            )
            matched, last_detail = _matches_wait_condition(
                desktop_state=desktop_state,
                condition=normalized,
                text=text,
                window_name=window_name,
            )
            if matched:
                elapsed = time.monotonic() - started_at
                return (
                    f"WaitFor condition '{normalized}' satisfied after "
                    f"{elapsed:.2f}s and {attempts} attempt(s): {last_detail}."
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out after {timeout:.2f}s waiting for '{normalized}': {last_detail}."
                )
            time.sleep(min(interval, remaining))
