"""Window-bound capture reuse and stale visual rejection on the real test window."""

import json

import win32gui

from windows_mcp.vision.capture_worker import worker


def test_repeated_native_frames_share_one_process(app, call):
    first = json.loads(call("Visual", window=str(app.hwnd), fallback_reason="canvas", backend="printwindow")[0])
    initial = worker.status()
    second = json.loads(call("Visual", window=str(app.hwnd), fallback_reason="canvas", backend="printwindow",
                             previous_target=first["target"], only_if_changed=True)[0])
    final = worker.status()
    assert final["pid"] == initial["pid"] and final["starts"] == initial["starts"]
    assert final["requests"] == initial["requests"] + 1
    assert not second["image_included"]


def test_visual_point_changed_after_capture_is_not_clicked(app, call):
    call("App", mode="switch", handle=app.hwnd)
    capture = json.loads(call("Visual", window=str(app.hwnd), fallback_reason="canvas", backend="printwindow")[0])
    # The test changes its own button label, keeping exactly the same window geometry.
    button = win32gui.FindWindowEx(app.hwnd, 0, "Button", "Save")
    assert button
    win32gui.SetWindowText(button, "XXXXX")
    win32gui.UpdateWindow(button)
    x, y = app.center("save")
    left, top, right, bottom = capture["desktop_bounds"]
    width, height = capture["image_size"]
    point = [(x - left) * width / (right - left), (y - top) * height / (bottom - top)]
    since = app.mark()
    result = call("Execute", target=capture["target"], action="click", mode="visual", value=json.dumps(point))
    assert result["status"] == "TARGET_STALE", result
    assert result["dispatch"]["button_downs"] == 0
    assert not any(event.get("control") == "save" for event in app.events(since))


def test_unchanged_visual_point_can_be_clicked(app, call):
    call("App", mode="switch", handle=app.hwnd)
    capture = json.loads(call("Visual", window=str(app.hwnd), fallback_reason="canvas", backend="printwindow")[0])
    x, y = app.center("save")
    left, top, right, bottom = capture["desktop_bounds"]
    width, height = capture["image_size"]
    point = [(x - left) * width / (right - left), (y - top) * height / (bottom - top)]
    since = app.mark()
    result = call("Execute", target=capture["target"], action="click", mode="visual", value=json.dumps(point))
    assert result["dispatch"]["button_downs"] == 1, result
    assert app.wait_for(lambda event: event.get("control") == "save" and event.get("count") == 1, since)
