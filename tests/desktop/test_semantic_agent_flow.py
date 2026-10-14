"""Real UIA engine, independent native file verification, Watch recovery and visual grounding."""

import asyncio
import json

import pytest
import win32gui

from windows_mcp.engine.sessions import get_runtime


def _node(observation, name, role=None):
    return next(item for item in observation["elements"] if item["name"] == name and (role is None or item["role"] == role))


def test_semantic_observe_execute_and_verify_native_field(app, call):
    observation = call("Observe", window=str(app.hwnd))
    field = _node(observation, "Name", "Edit")
    result = call("Execute", target=field["element_id"], action="set_value", value="语义控制 🌍",
                  expected={"kind": "element_state", "target": field["element_id"], "state": {"value": "语义控制 🌍"}})
    assert result["status"] == "VERIFIED" and "UIA" in result["method"]
    assert app.wait_for(lambda event: event.get("control") == "name" and event.get("value") == "语义控制 🌍", 0)
    assert call("Verify", expected={"kind": "element_state", "target": field["element_id"], "state": {"value": "语义控制 🌍"}})["status"] == "VERIFIED"


def test_native_file_is_verified_and_scope_is_enforced(tmp_path, call):
    runtime = get_runtime()
    previous = runtime.policy.roots
    runtime.policy.roots = [tmp_path.resolve()]
    try:
        path = str(tmp_path / "semantic.txt")
        result = call("NativeAPI", operation="write", path=path, content="跨通道验证 🌍")
        assert result["status"] == "VERIFIED"
        assert call("Verify", expected={"kind": "file_content", "path": path, "content": "跨通道验证 🌍"})["status"] == "VERIFIED"
        with pytest.raises(PermissionError, match="outside"):
            call("NativeAPI", operation="read", path=str(tmp_path.parent / "outside.txt"))
    finally:
        runtime.policy.roots = previous


def test_two_watches_recover_without_events_and_release_subscriptions(make_app, tools):
    a, b = make_app(), make_app()
    runtime = get_runtime()
    from windows_mcp.watchdog.subscription import SubscriptionHub

    hub = SubscriptionHub()
    previous = runtime.watchdog
    runtime.watchdog = type("Dog", (), {"subscriptions": hub})()

    async def run():
        initial_a = await tools["Observe"](window=str(a.hwnd))
        initial_b = await tools["Observe"](window=str(b.hwnd))
        field_a = _node(initial_a, "Name", "Edit")["element_id"]
        field_b = _node(initial_b, "Name", "Edit")["element_id"]
        watching = [asyncio.create_task(tools["Watch"](window=str(app.hwnd), expected={"kind": "element_state", "target": target, "state": {"value": value}}, timeout_ms=4000))
                    for app, target, value in [(a, field_a, "one"), (b, field_b, "two")]]
        await asyncio.sleep(0.5)
        for app, value in [(a, "one"), (b, "two")]:
            control = win32gui.FindWindowEx(app.hwnd, 0, "Edit", None)
            win32gui.SendMessage(control, 0x000C, 0, value)  # fixture-only WM_SETTEXT; no UIA events published
        results = await asyncio.gather(*watching)
        assert all(result["status"] == "VERIFIED" for result in results)
        assert results[0]["subscription_id"] != results[1]["subscription_id"]
        assert hub.active == 0

    try:
        asyncio.run(run())
    finally:
        runtime.watchdog = previous


def test_watch_cancellation_is_bounded_and_releases_subscription(app, tools):
    async def run():
        watching = asyncio.create_task(tools["Watch"](window=str(app.hwnd), timeout_ms=5000))
        await asyncio.sleep(0.3)
        await tools["Session"](operation="cancel")
        result = await asyncio.wait_for(watching, timeout=2)
        assert result["status"] == "CANCELLED"
        await tools["Session"](operation="resume")

    try:
        asyncio.run(run())
    finally:
        get_runtime().current().cancelled.clear()


def test_visual_coordinates_are_bound_to_unchanged_owned_window(app, call):
    call("App", mode="switch", handle=app.hwnd)
    captured = call("Visual", window=str(app.hwnd), fallback_reason="canvas")
    metadata = json.loads(captured[0])
    left, top, _, _ = metadata["desktop_bounds"]
    x, y = app.center("canvas")
    since = app.mark()
    result = call("Execute", target=metadata["target"], mode="visual", action="click", value=json.dumps([x - left, y - top]))
    assert result["status"] == "DISPATCHED_UNVERIFIED"
    assert app.wait_for(lambda event: event.get("kind") == "left_down", since)
    win32gui.MoveWindow(app.hwnd, left + 10, top + 10, 900, 720, True)
    moved = call("Execute", target=metadata["target"], mode="visual", action="click", value=json.dumps([x - left, y - top]))
    assert moved["status"] == "TARGET_STALE" and not moved["input_dispatched"]


def test_actual_mta_events_are_delivered_to_independent_subscribers(app, call):
    import time
    from windows_mcp.watchdog.service import WatchDog

    dog = WatchDog()
    first = dog.subscriptions.subscribe("owner", app.hwnd, ["focus"], ttl=5)
    second = dog.subscriptions.subscribe("owner", app.hwnd, ["focus"], ttl=5)
    dog.start()
    try:
        deadline = time.monotonic() + 2
        while dog._focus_handler is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert dog._focus_handler is not None
        call("Act", target="Name", control_type="Edit", action="focus", via="uia", window=str(app.hwnd))
        assert first.signal.wait(2), "UIA focus event did not arrive"
        assert second.signal.wait(2), "Second subscriber was overwritten"
        assert dog.subscriptions.drain(first)[0][0]["window_id"] == app.hwnd
        assert dog.subscriptions.drain(second)[0][0]["window_id"] == app.hwnd
    finally:
        dog.subscriptions.cancel(first.subscription_id, "owner")
        dog.subscriptions.cancel(second.subscription_id, "owner")
        dog.stop()
    assert dog.subscriptions.active == 0
