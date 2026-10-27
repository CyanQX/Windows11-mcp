"""Watch idles off the desktop worker and never mistakes an event for success."""

import asyncio
from types import SimpleNamespace

import pytest

from tests import test_semantic_engine
from windows_mcp.desktop import elements
from windows_mcp.engine.watch import watch
from windows_mcp.watchdog.subscription import SubscriptionHub

observed = test_semantic_engine.observed


@pytest.fixture
def setup_watch(observed):
    desktop, session, runtime, element, first = observed
    hub = SubscriptionHub()
    runtime.watchdog = SimpleNamespace(subscriptions=hub)
    return desktop, session, runtime, element, first, hub


async def test_idle_short_watch_scans_once_and_frees_worker(setup_watch, monkeypatch):
    desktop, session, runtime, _, _, hub = setup_watch
    from windows_mcp.engine import watch as module
    from windows_mcp.runtime import run_on_desktop

    original, calls = module.observe_desktop, []
    def scan(*a, **kw):
        calls.append(True)
        return original(*a, **kw)
    monkeypatch.setattr(module, "observe_desktop", scan)
    task = asyncio.create_task(watch(desktop, runtime, session, window="100", timeout_ms=300))
    await asyncio.sleep(.05)
    assert await asyncio.wait_for(run_on_desktop(lambda: "free"), .5) == "free"
    assert (await task)["status"] == "TIMEOUT"
    assert calls == [True] and hub.active == 0


async def test_control_watch_uses_only_pinned_read_and_recovers_lost_events(setup_watch, monkeypatch):
    desktop, session, runtime, element, first, hub = setup_watch
    from windows_mcp.engine import watch as module

    monkeypatch.setattr(module, "FALLBACK_SECONDS", .08)
    monkeypatch.setattr(module, "observe_desktop", lambda *a, **k: pytest.fail("control watch must not scan"))
    expected = {"kind": "element_state", "target": first["elements"][0]["element_id"], "state": {"value": "new"}}
    task = asyncio.create_task(watch(desktop, runtime, session, expected=expected, timeout_ms=800))
    await asyncio.sleep(.03)
    element.state["value"] = "new"
    result = await task
    assert result["status"] == "VERIFIED" and result["verification"]["passed"] is True
    assert hub.active == 0


async def test_event_alone_does_not_verify_and_subscriptions_are_removed(setup_watch):
    desktop, session, runtime, _, first, hub = setup_watch
    expected = {"kind": "element_state", "target": first["elements"][0]["element_id"], "state": {"value": "new"}}
    task = asyncio.create_task(watch(desktop, runtime, session, expected=expected, timeout_ms=250))
    await asyncio.sleep(.03)
    hub.publish("property", 100, runtime_id=(1, 2, 3))
    result = await task
    assert result["status"] == "TIMEOUT" and result["events"]
    assert hub.active == 0


async def test_property_change_refreshes_without_tree_scan(setup_watch, monkeypatch):
    desktop, session, runtime, element, _, hub = setup_watch
    calls = []
    original = elements.walk_bounded
    monkeypatch.setattr(elements, "walk_bounded", lambda *a, **k: (calls.append(True) or original(*a, **k)))
    task = asyncio.create_task(watch(desktop, runtime, session, window="100", timeout_ms=800))
    await asyncio.sleep(.05)
    element.state["value"] = "new"
    hub.publish("property", 100, runtime_id=element.runtime_id)
    result = await task
    assert result["status"] == "VERIFIED" and len(calls) == 1
    assert result["observation"]["context"]["coverage"] == "known_targets_only"


async def test_cancellation_epoch_does_not_revive_watch(setup_watch):
    desktop, session, runtime, _, _, hub = setup_watch
    task = asyncio.create_task(watch(desktop, runtime, session, window="100", timeout_ms=1000))
    await asyncio.sleep(.04)
    session.cancel()
    session.cancelled.clear()
    assert (await asyncio.wait_for(task, .5))["status"] == "CANCELLED"
    assert hub.active == 0


async def test_event_burst_cannot_starve_fallback_scan(setup_watch, monkeypatch):
    desktop, session, runtime, element, _, hub = setup_watch
    from windows_mcp.engine import watch as module

    monkeypatch.setattr(module, "FALLBACK_SECONDS", .08)
    original, full_scans = elements.walk_bounded, []
    monkeypatch.setattr(elements, "walk_bounded", lambda *a, **kw: (full_scans.append(True) or original(*a, **kw)))
    task = asyncio.create_task(watch(desktop, runtime, session, window="100", timeout_ms=300))
    for _ in range(12):
        await asyncio.sleep(.02)
        hub.publish("property", 100, runtime_id=element.runtime_id)
    assert (await task)["status"] == "TIMEOUT"
    assert len(full_scans) >= 3
