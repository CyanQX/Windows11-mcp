"""Real fixture-only delivery checks for the unified pointer backend."""

from __future__ import annotations

import json
import ctypes
import ctypes.wintypes as wt

import pytest

from windows_mcp.desktop import native_input
from windows_mcp.engine.sessions import get_runtime
from tests.desktop.guard import assert_point_owned


def _clip_contains(*points):
    rect = wt.RECT()
    if not native_input._user32.GetClipCursor(ctypes.byref(rect)):
        pytest.skip("Windows cursor restriction could not be queried")
    return all(rect.left <= x < rect.right and rect.top <= y < rect.bottom for x, y in points)


def test_auto_pointer_click_reaches_the_button_and_reports_native_events(app, call):
    if not _clip_contains(app.center("save")):
        pytest.skip("System ClipCursor excludes the fixture button; release the cursor restriction to qualify real pointer input")
    runtime = get_runtime()
    previous = runtime.interaction_mode
    runtime.interaction_mode = "pointer"
    try:
        observation = call("Observe", window=app.title)
        button = next(item for item in observation["elements"] if item["name"] == "Save")
        since = app.mark()
        result = call("Execute", target=button["element_id"], action="click")
        assert result["input_dispatched"]
        assert result["dispatch"]["button_downs"] == result["dispatch"]["button_ups"] == 1, json.dumps(result)
        assert result["dispatch"]["native_events"] >= 2  # Already over the button needs no extra move.
        assert result["dispatch"]["uia_calls"] == 0
        received = app.wait_for(lambda event: event.get("control") == "save", since)
        assert received and received[-1]["count"] == 1
        bounds = button["bounds"]
        x, y = native_input.cursor_position()
        assert bounds["left"] <= x < bounds["right"] and bounds["top"] <= y < bounds["bottom"]
    finally:
        runtime.interaction_mode = previous


def test_public_drag_delivers_ordered_down_move_up_to_fixture(app, call):
    left, top, right, bottom = app.canvas
    start, end = (left + 30, top + 40), (right - 40, bottom - 45)
    if not _clip_contains(start, end):
        pytest.skip("System ClipCursor excludes the fixture drag path; release the cursor restriction to qualify dragging")
    assert_point_owned(*start, {app.pid})
    assert_point_owned(*end, {app.pid})
    since = app.mark()
    call("Move", loc=list(end), from_loc=list(start), drag=True, duration=0.2)
    received = app.wait_for(lambda event: event.get("kind") == "left_up", since)
    assert received
    events = app.events(since)
    down = next(index for index, event in enumerate(events) if event.get("kind") == "left_down")
    up = next(index for index, event in enumerate(events) if event.get("kind") == "left_up")
    assert (events[down]["x"], events[down]["y"]) == start
    assert (events[up]["x"], events[up]["y"]) == end
    assert any(event.get("kind") == "move" for event in events[down + 1:up])
    assert native_input.cursor_position() == end


def test_explicit_semantic_mode_reports_provider_call_without_mouse_input(app, call):
    runtime = get_runtime()
    previous = runtime.interaction_mode
    runtime.interaction_mode = "pointer"
    try:
        observation = call("Observe", window=app.title)
        button = next(item for item in observation["elements"] if item["name"] == "Save")
        since = app.mark()
        result = call("Execute", target=button["element_id"], action="click", mode="semantic")
        assert result["input_dispatched"] and result["dispatch"]["uia_calls"] == 1
        assert result["dispatch"]["native_events"] == result["dispatch"]["button_downs"] == 0
        assert app.wait_for(lambda event: event.get("control") == "save", since)
    finally:
        runtime.interaction_mode = previous


def test_existing_cursor_restriction_refuses_a_press_at_unreachable_target(app, call):
    if _clip_contains(app.center("save")):
        pytest.skip("No existing cursor restriction excludes the fixture; this test never changes system ClipCursor")
    observation = call("Observe", window=app.title)
    button = next(item for item in observation["elements"] if item["name"] == "Save")
    since = app.mark()
    result = call("Execute", target=button["element_id"], action="click", mode="physical")
    assert result["status"] == "FAILED", json.dumps(result)
    assert result["dispatch"]["button_downs"] == result["dispatch"]["button_ups"] == 0
    assert any("INPUT_TARGET_UNREACHABLE" in error for error in result["errors"])
    assert not any(event.get("control") == "save" for event in app.events(since))
