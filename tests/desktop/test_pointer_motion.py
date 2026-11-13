"""Natural motion, live target relocation and external-input handoff on the real fixture."""

from __future__ import annotations

import json
import time

import pytest
import win32con
import win32gui

from windows_mcp.desktop import native_input
from windows_mcp.engine.sessions import get_runtime
from tests.desktop.guard import assert_point_owned
from tests.desktop.test_pointer_batch_four import _clip_contains


def test_natural_pointer_follows_control_that_moves_during_travel(app, call):
    if not _clip_contains(app.center("save"), app.center("canvas")):
        pytest.skip("Cursor bounds exclude fixture targets; run with --fixture-cursor")
    call("Move", loc=list(app.center("canvas")))
    observation = call("Observe", window=app.title)
    target = next(item for item in observation["elements"] if item["name"] == "Save")
    since = app.mark()
    win32gui.PostMessage(app.hwnd, win32con.WM_APP + 31, 140, 0)
    result = call("Execute", target=target["element_id"], action="click", mode="physical")
    assert result["dispatch"]["button_downs"] == 1, json.dumps(result)
    assert result["dispatch"]["motion"]["replans"] >= 1, json.dumps(result)
    assert result["dispatch"]["motion"]["samples"] > 5
    assert app.wait_for(lambda event: event.get("control") == "save" and event.get("count") == 1, since)
    moved = next(event for event in app.events(since) if event["ev"] == "target_moved")
    x, y = native_input.cursor_position()
    left, top, right, bottom = moved["bounds"]
    assert left <= x < right and top <= y < bottom


def test_hover_has_a_real_bounded_dwell_without_button_input(app, call):
    point = app.center("canvas")
    if not _clip_contains(point):
        pytest.skip("Cursor bounds exclude fixture target")
    assert_point_owned(*point, {app.pid})
    since = app.mark()
    start = time.monotonic()
    call("Click", loc=list(point), clicks=0)
    assert time.monotonic() - start >= 0.38
    assert not any(event.get("kind") == "left_down" for event in app.events(since))
    motion = get_runtime().current().last_pointer_motion
    assert motion["style"] == "natural" and motion["elapsed_ms"] >= 390


def test_external_process_input_during_drag_stops_and_releases(app, call):
    left, top, right, bottom = app.canvas
    start, end = (left + 40, top + 45), (right - 40, bottom - 40)
    if not _clip_contains(start, end):
        pytest.skip("Cursor bounds exclude fixture drag path")
    assert_point_owned(*start, {app.pid})
    assert_point_owned(*end, {app.pid})
    since = app.mark()
    win32gui.SendMessage(app.hwnd, win32con.WM_APP + 32, 0, 0)
    try:
        with pytest.raises(RuntimeError, match="USER_INTERRUPTED|CANCELLED"):
            call("Move", loc=list(end), from_loc=list(start), drag=True, duration=0.6)
        events = app.wait_for(lambda event: event.get("kind") == "left_up", since)
        assert events
        assert app.wait_for(lambda event: event["ev"] == "external_intervention", since)
        session = get_runtime().current()
        assert session.cancelled.is_set() and session.pointer_interruption == "external_keyboard_input"
        assert not native_input._user32.GetAsyncKeyState(1) & 0x8000
    finally:
        call("Session", operation="resume")
