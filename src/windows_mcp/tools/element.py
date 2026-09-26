"""Find and Act -- operate real controls instead of guessing pixels.

``Find`` searches a live window through UI Automation and returns short references (``e1`` ...).
``Act`` performs a semantic action on a reference, a Snapshot label or a control name, through
native UIA patterns or real mouse/keyboard input, then reads the control back and reports what
actually happened.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from windows_mcp.desktop import elements
from windows_mcp.infrastructure import with_analytics

ActAction = Literal[
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
]


def _as_bool(value: bool | str, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{name} must be true or false")


def _windows_for(window: str | int | None) -> list[elements.TopWindow]:
    windows = elements.resolve_windows(window)
    if not windows:
        raise elements.ElementNotFound(
            f"No open window matches {window!r}. Open windows: {elements.describe_windows()}"
        )
    return windows


def _line(element: elements.Element) -> str:
    x, y = element.center
    caps = ",".join(sorted(element.capabilities & {"invoke", "toggle", "select", "expand", "value", "range", "scroll"}))
    extra = []
    if element.automation_id:
        extra.append(f"id={element.automation_id}")
    state = element.state_text()
    if state:
        extra.append(state)
    return (
        f"{element.ref:<5} {element.label}  at ({x},{y})"
        + (f"  [{caps}]" if caps else "")
        + (f"  {' '.join(extra)}" if extra else "")
    )


def find_elements(
    desktop: Any,
    *,
    name: str | None,
    control_type: str | None,
    automation_id: str | None,
    window: str | int | None,
    limit: int,
    include_offscreen: bool,
) -> str:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    windows = _windows_for(window)
    matches, scanned = elements.search(
        windows[:5],
        name=name,
        control_type=control_type,
        automation_id=automation_id,
        include_offscreen=include_offscreen,
        limit=limit,
    )
    for match in matches:
        desktop.element_registry.register(match)
    scope = ", ".join(w.label for w in windows[:5])
    query = " ".join(
        part
        for part in (
            f"name~{name!r}" if name else "",
            f"type={control_type}" if control_type else "",
            f"id={automation_id!r}" if automation_id else "",
        )
        if part
    ) or "all elements"
    if not matches:
        hint = "" if include_offscreen else " (offscreen elements are hidden; try include_offscreen=true)"
        return (
            f"No element matched {query} in {scope} ({scanned} elements scanned){hint}. "
            f"Open windows: {elements.describe_windows()}"
        )
    lines = [f"{len(matches)} match(es) for {query} in {scope} ({scanned} elements scanned):"]
    lines += [_line(match) for match in matches]
    lines.append(
        'Next: Act(target="e#", action=...). References are re-checked live before every action.'
    )
    return "\n".join(lines)


def resolve_target(
    desktop: Any,
    target: str | int,
    *,
    window: str | int | None = None,
    control_type: str | None = None,
) -> elements.Element:
    """``e12`` reference, Snapshot label (int or "12" / "#12"), or a control name."""
    text = str(target).strip()
    if not text:
        raise ValueError("target is required: an element reference (e5), a Snapshot label (12) or a name")
    if text.lower().startswith("e") and text[1:].isdigit():
        return elements.resolve_ref(desktop.element_registry, text)
    label_text = text[1:] if text.startswith("#") else text
    if isinstance(target, int) or label_text.isdigit():
        element = desktop.locate_label(int(label_text))
        if element is None:
            raise elements.ElementError(
                f"label {label_text} is a word box without its own UI element; use Click with the label instead."
            )
        desktop.element_registry.register(element)
        return element
    windows = _windows_for(window)
    matches, _ = elements.search(windows[:5], name=text, control_type=control_type, include_offscreen=True, limit=8)
    if not matches:
        raise elements.ElementNotFound(
            f"No control named {text!r} in {', '.join(w.label for w in windows[:5])}. "
            "Use Find to list what is there."
        )
    best = elements.name_score(text, matches[0])
    ties = [m for m in matches if elements.name_score(text, m) == best]
    visible_ties = [m for m in ties if not m.offscreen] or ties
    if len(visible_ties) > 1:
        for match in visible_ties:
            desktop.element_registry.register(match)
        options = "\n".join(_line(m) for m in visible_ties[:8])
        raise elements.AmbiguousTarget(
            f"{len(visible_ties)} controls match {text!r} equally well -- pick one by reference "
            f"(or pass control_type/window):\n{options}"
        )
    chosen = visible_ties[0]
    desktop.element_registry.register(chosen)
    return chosen


def act_on(
    desktop: Any,
    *,
    target: str | int,
    action: str,
    value: str | None,
    window: str | int | None,
    control_type: str | None,
    wait: float,
    via: str = "auto",
) -> str:
    element = resolve_target(desktop, target, window=window, control_type=control_type)
    if element.window_handle == 0:
        element.window_handle = elements.root_at(*element.center)
    before_state = element.state_text()
    observe_focus = action not in {"type", "set_value"}
    before = elements.observe(include_focus=observe_focus)
    outcome = elements.act(desktop, element, action, value, via=via)
    changes = elements.wait_for_changes(before, timeout=max(0.0, min(wait, 5.0)), include_focus=observe_focus)
    final = outcome.element or element
    if final.ref is None:
        desktop.element_registry.register(final)
    lines = [
        f'{action} -> {final.label} ({final.ref or element.ref}) in "{final.window_title or element.window_title}": '
        f"{outcome.method}."
    ]
    if outcome.notes:
        lines.append("Notes: " + "; ".join(outcome.notes) + ".")
    if outcome.verified is True:
        lines.append(f"Verified: {outcome.detail or 'the control reached the requested state'}.")
    elif outcome.verified is False:
        lines.append(f"NOT verified: {outcome.detail or 'the control did not reach the requested state'}. Look again before retrying.")
    elif outcome.detail:
        lines.append(f"Result: {outcome.detail}.")
    after_state = final.state_text() if outcome.element is not None else ""
    if outcome.element is not None and after_state != before_state and outcome.verified is None:
        lines.append(f"Control state: {before_state or '-'} -> {after_state or '-'}.")
    lines.append(elements.effects_line(changes))
    return "\n".join(lines)


def register(mcp: Any, *, get_desktop: Callable[[], Any], get_analytics: Callable[[], Any]) -> None:
    @mcp.tool(
        name="Find",
        description=(
            "Find controls in a REAL window by name / control type / automation id, live through "
            "UI Automation (one fast query, no screenshot). Keywords: find button, locate field, "
            "search element, which controls, inspect window. Returns references like e5 with type, "
            "name, center coordinates, supported actions ([invoke,toggle,select,expand,value,range,"
            "scroll]) and current state (value, checked, selected, expanded, disabled). window: title "
            "text, process name ('notepad'), handle, 'taskbar', '*' for all windows; default = the "
            "foreground window. Use the references with Act."
        ),
        annotations=ToolAnnotations(
            title="Find",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Find-Tool")
    def find_tool(
        name: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        window: str | None = None,
        limit: int = 15,
        include_offscreen: bool | str = False,
        ctx: Context = None,
    ) -> str:
        return find_elements(
            get_desktop(),
            name=name,
            control_type=control_type,
            automation_id=automation_id,
            window=window,
            limit=limit,
            include_offscreen=_as_bool(include_offscreen, "include_offscreen"),
        )

    @mcp.tool(
        name="Act",
        description=(
            "Operate a REAL control the way a person would and verify the result. target: an element "
            "reference from Find (e5), a Snapshot label (12), or the control's name ('Save'). "
            "Actions: click / double_click / right_click / hover (real mouse, after scrolling the "
            "control into view, bringing its window forward and hit-testing the exact point), toggle "
            "(value on/off optional; clicks until the state matches), select (value = item name for "
            "combo boxes / lists / tabs: opens the drop-down and clicks the item), expand / collapse, "
            "set_value (replace text; value), type (append text; value), set_range (slider/spinner "
            "number; value), invoke, focus, scroll_into_view. via: 'auto' (default: real mouse/keyboard "
            "first so the app runs its own handlers, UIA pattern as fallback), 'input' (real input "
            "only), 'uia' (UIA patterns only: works on covered windows and never moves the user's "
            "pointer). Returns the method used, whether the new state was verified by reading the "
            "control back, and the visible effects (windows opened/closed, focus). Keyboard text is "
            "only sent when the control's window is confirmed in the foreground."
        ),
        annotations=ToolAnnotations(
            title="Act",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Act-Tool")
    def act_tool(
        target: str,
        action: ActAction = "click",
        value: str | None = None,
        window: str | None = None,
        control_type: str | None = None,
        via: Literal["auto", "input", "uia"] = "auto",
        wait: float = 0.6,
        ctx: Context = None,
    ) -> str:
        return act_on(
            get_desktop(),
            target=target,
            action=action,
            value=value,
            window=window,
            control_type=control_type,
            wait=wait,
            via=via,
        )
