"""Bounded TextPattern reads, verified selection and conservative local editing.

TextPattern cannot modify document text. Editing uses an independently checked selection and
real keyboard input; it never rewrites a whole rich document through ValuePattern.
All provider work runs on the desktop thread or a tracked action helper.
"""

from __future__ import annotations

import time

from windows_mcp.desktop import elements as el
from windows_mcp.desktop import native_input
from windows_mcp.engine.contracts import TextRangeSpec
from windows_mcp.uia.enums import PatternId, TextAttributeId, TextUnit

_START, _END = 0, 1
_DOCUMENT_LIMIT = 100000
_UNITS = {"character": TextUnit.Character, "word": TextUnit.Word, "line": TextUnit.Line,
          "paragraph": TextUnit.Paragraph, "document": TextUnit.Document}


def _length(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _prefix(text: str, units: int) -> str:
    return text.encode("utf-16-le", "surrogatepass")[:units * 2].decode("utf-16-le", "ignore")


def pattern_for(element):
    if element.password:
        raise el.ElementError("Sensitive fields do not expose text ranges or local edits")
    pattern = el.get_pattern(element.raw, PatternId.TextPattern)
    if pattern is None:
        raise el.ElementError("This control does not expose TextPattern; use supported value actions")
    return pattern


def selections(pattern) -> list:
    ranges = pattern.GetSelection()
    if ranges.Length > 16:
        raise el.ElementError("Too many disjoint text selections; refine the selection first")
    return [ranges.GetElement(index) for index in range(ranges.Length)]


def same_range(a, b) -> bool:
    return a.CompareEndpoints(_START, b, _START) == 0 and a.CompareEndpoints(_END, b, _END) == 0


def locate(pattern, spec: TextRangeSpec):
    from windows_mcp.engine.sessions import _bound

    session = _bound.get()
    deadline = time.monotonic() + 3.0
    document = pattern.DocumentRange
    if spec.text is not None:
        remaining = document.Clone()
        chosen = None
        count = 0
        while count < (spec.occurrence or 2):
            if session is not None:
                session.check()
            if time.monotonic() >= deadline:
                raise el.ElementError("Text search reached its time budget; no selection was changed")
            found = remaining.FindText(spec.text, False, spec.ignore_case)
            if not found:
                break
            count += 1
            chosen = found
            if spec.occurrence == count:
                return found
            if found.CompareEndpoints(_START, found, _END) == 0:
                raise el.ElementError("Provider returned an empty text match")
            remaining.MoveEndpointByRange(_START, found, _END)
        if count == 0 or (spec.occurrence is not None and count < spec.occurrence):
            raise el.ElementNotFound("Requested text occurrence was not found")
        if count > 1:
            raise el.AmbiguousTarget("Text matches more than once; supply a one-based occurrence")
        return chosen
    if spec.unit == "document":
        return document.Clone()
    selected = document.Clone()
    selected.MoveEndpointByRange(_END, document, _START)
    unit = _UNITS[spec.unit]
    moved = selected.MoveEndpointByUnit(_START, unit, spec.index - 1) if spec.index > 1 else 0
    if moved != spec.index - 1 or selected.MoveEndpointByUnit(_END, unit, spec.count) != spec.count:
        raise el.ElementNotFound("Requested text unit range exceeds the document")
    return selected


def read(element, *, selection: bool = False, spec: dict | TextRangeSpec | None = None,
         max_chars: int = 4000) -> dict:
    if not 1 <= max_chars <= 10000:
        raise ValueError("max_chars must be 1..10000")
    pattern = pattern_for(element)
    if spec is not None and selection:
        raise ValueError("Text search and reading the current selection are separate modes")
    ranges = selections(pattern) if selection else [locate(pattern, TextRangeSpec.model_validate(spec)) if spec is not None else pattern.DocumentRange]
    values, remaining, truncated = [], max_chars, False
    for selected in ranges:
        text = selected.GetText(remaining + 1)
        truncated |= _length(text) > remaining
        values.append(_prefix(text, remaining))
        remaining -= _length(values[-1])
        if remaining == 0:
            truncated |= len(values) < len(ranges)
            break
    return {"selection" if selection else "text": values if selection else (values[0] if values else ""),
            "text_truncated": truncated, "text_source": "uia_text_pattern"}


def _selected(pattern, wanted) -> bool:
    actual = selections(pattern)
    return len(actual) == 1 and same_range(actual[0], wanted)


def _focus_matches(element) -> bool:
    focused = el.focused_element()
    return focused is not None and bool(el._client().CompareElements(focused.raw, element.raw))


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def act(desktop, element, action: str, value: str | None, *, spec=None, via: str = "auto"):
    pattern = pattern_for(element)
    if not pattern.SupportedTextSelection:
        raise el.ElementError("Provider does not support text selection")
    if action == "select_text":
        if spec is None:
            if not value:
                raise ValueError("select_text requires text range options or a literal value")
            spec = {"text": value}
        wanted = locate(pattern, TextRangeSpec.model_validate(spec))
        el.call_with_timeout(wanted.Select)
        return el.ActOutcome("UIA TextRange.Select", _selected(pattern, wanted),
                             "Selection endpoints were read back", [], element)
    if via == "uia":
        raise el.ElementError("Local text modification needs physical input; use auto or physical mode")
    if action not in {"replace_text", "delete_text", "append_text"}:
        raise ValueError("Unsupported text range action")
    replacement = "" if action == "delete_text" else value
    if replacement is None or len(replacement) > 10000 or "\t" in replacement:
        raise ValueError("Local edit requires value of at most 10000 characters without Tab")
    document = pattern.DocumentRange
    original = document.GetText(_DOCUMENT_LIMIT + 1)
    if _length(original) > _DOCUMENT_LIMIT:
        raise el.ElementError("Document exceeds the local-edit verification limit; use an application API")
    if element.state.get("read_only") is True:
        raise el.ElementError("This control is read-only")
    readonly = document.GetAttributeValue(TextAttributeId.IsReadOnlyAttribute)
    if readonly is not False and readonly != 0:
        raise el.ElementError("Provider did not establish an editable text range")
    if action == "append_text":
        if spec is not None:
            raise ValueError("append_text appends at document end and does not accept range options")
        wanted = document.Clone()
        wanted.MoveEndpointByRange(_START, document, _END)
    elif spec is not None:
        wanted = locate(pattern, TextRangeSpec.model_validate(spec))
    else:
        current = selections(pattern)
        if len(current) != 1 or current[0].CompareEndpoints(_START, current[0], _END) == 0:
            raise el.AmbiguousTarget("Local edit requires one nonempty selection or explicit range options")
        wanted = current[0]
    prefix, suffix = document.Clone(), document.Clone()
    prefix.MoveEndpointByRange(_END, wanted, _START)
    suffix.MoveEndpointByRange(_START, wanted, _END)
    expected = prefix.GetText(_DOCUMENT_LIMIT + 1) + replacement + suffix.GetText(_DOCUMENT_LIMIT + 1)
    if _length(expected) > _DOCUMENT_LIMIT:
        raise el.ElementError("Edited document would exceed the readback verification limit")
    if "\n" in _normalize(replacement) and element.control_type == "Edit":
        handle = el._native_handle(element)
        import win32con
        import win32gui

        if not handle or not win32gui.GetWindowLong(handle, win32con.GWL_STYLE) & win32con.ES_MULTILINE:
            raise el.ElementError("Newline insertion requires a confirmed multiline control")
    el.ensure_foreground(desktop, element.window_handle)
    el.call_with_timeout(element.raw.SetFocus)
    el.call_with_timeout(wanted.Select)
    if (not _focus_matches(element) or not _selected(pattern, wanted)
            or pattern.DocumentRange.GetText(_DOCUMENT_LIMIT + 1) != original):
        raise el.StaleTarget("Text, focus or selection changed before input; no replacement was sent")
    if replacement:
        native_input.type_text(replacement, chunk_chars=10000, pause=0)
    elif wanted.CompareEndpoints(_START, wanted, _END) != 0:
        native_input.press_keys("backspace")
    actual = pattern.DocumentRange.GetText(_DOCUMENT_LIMIT + 1)
    return el.ActOutcome("UIA TextRange.Select + SendInput", _normalize(actual) == _normalize(expected),
                         "Whole text readback matched the requested local edit" if _normalize(actual) == _normalize(expected)
                         else "Text readback differed; do not repeat the edit without observing", [], el.refresh(element) or element)
