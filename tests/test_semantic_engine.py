"""Identity, deltas and independent verification over mocked provider responses."""

from types import SimpleNamespace

import pytest

from tests.test_elements import make
from windows_mcp.desktop import elements
from windows_mcp.engine.action_router import execute
from windows_mcp.engine.observe_router import observe_desktop
from windows_mcp.engine.sessions import EngineRuntime, SessionState
from windows_mcp.engine.target_resolver import resolve
from windows_mcp.infrastructure.policy import PolicyGate
from windows_mcp.vision.grounding import ScreenshotSpace


@pytest.fixture
def observed(monkeypatch, tmp_path):
    element = make(window_handle=100, pid=1000, state={"value": "old"}, capabilities=frozenset({"value"}))
    window = elements.TopWindow(100, "Test", 1000, "test", False)
    monkeypatch.setattr(elements, "resolve_windows", lambda window=None: [observed_window] if observed_window else [])
    observed_window = window
    monkeypatch.setattr(elements, "foreground_root", lambda: 100)
    monkeypatch.setattr(elements, "window_info", lambda handle: window if handle == 100 else None)
    monkeypatch.setattr(elements, "walk_bounded", lambda *args, **kwargs: ([element], False, []))
    monkeypatch.setattr(elements, "refresh", lambda old: element)
    desktop = SimpleNamespace(element_registry=elements.ElementRegistry())
    session = SessionState()
    runtime = EngineRuntime(PolicyGate(roots=[tmp_path]))
    first = observe_desktop(desktop, session, window="100")
    return desktop, session, runtime, element, first


def test_observation_exposes_source_and_scoped_revision(observed):
    _, session, _, _, first = observed
    assert first["context"]["source"] == "uia"
    assert first["session_id"] == session.session_id
    assert first["elements"][0]["element_id"].startswith(f"uia:{session.session_id}:1:")


def test_changed_revision_invalidates_prior_targets_and_returns_delta(observed):
    desktop, session, _, element, first = observed
    old_target = first["elements"][0]["element_id"]
    element.state = {"value": "changed"}
    delta = observe_desktop(desktop, session, window="100", since_revision=first["revision"])
    assert delta["revision"] > first["revision"]
    assert delta["context"]["delta"] and delta["context"]["refresh_targets"]
    with pytest.raises(elements.StaleTarget):
        resolve(desktop, session, old_target)


def test_unchanged_observation_has_empty_delta(observed):
    desktop, session, _, _, first = observed
    delta = observe_desktop(desktop, session, window="100", since_revision=first["revision"])
    assert delta["revision"] == first["revision"]
    assert delta["elements"] == []


def test_target_is_not_accepted_from_another_client(observed):
    desktop, _, _, _, first = observed
    with pytest.raises(elements.StaleTarget):
        resolve(desktop, SessionState(), first["elements"][0]["element_id"])


def test_reused_window_handle_is_stale(observed, monkeypatch):
    desktop, session, _, _, first = observed
    monkeypatch.setattr(elements, "window_info", lambda handle: elements.TopWindow(100, "Test", 9999, "test", False))
    with pytest.raises(elements.StaleTarget):
        resolve(desktop, session, first["elements"][0]["element_id"])


def test_auto_uses_supported_semantic_pattern(observed, monkeypatch):
    desktop, session, runtime, element, first = observed
    methods = []

    def act(desktop, target, action, value, *, via):
        methods.append(via)
        target.state = {"value": value}
        return elements.ActOutcome("UIA SetValue", True, "", [], element)

    monkeypatch.setattr(elements, "act", act)
    result = execute(desktop, runtime, session, target=first["elements"][0]["element_id"], action="set_value", value="new")
    assert methods == ["uia"]
    assert result["status"] == "VERIFIED"


def test_dispatch_is_not_mistaken_for_business_success(observed, monkeypatch):
    desktop, session, runtime, element, first = observed
    monkeypatch.setattr(elements, "act", lambda *args, **kwargs: elements.ActOutcome("UIA Invoke", None, "", [], element))
    result = execute(desktop, runtime, session, target=first["elements"][0]["element_id"], action="click")
    assert result["input_dispatched"]
    assert result["status"] == "DISPATCHED_UNVERIFIED"
    assert result["verification"]["passed"] is None


def test_explicit_missing_file_is_not_verified(observed, monkeypatch, tmp_path):
    desktop, session, runtime, element, first = observed
    monkeypatch.setattr(elements, "act", lambda *args, **kwargs: elements.ActOutcome("UIA Invoke", None, "", [], element))
    result = execute(desktop, runtime, session, target=first["elements"][0]["element_id"], action="click",
                     expected={"kind": "file_exists", "path": str(tmp_path / "missing.txt")})
    assert result["status"] == "DISPATCHED_UNVERIFIED"
    assert result["verification"]["passed"] is False


def test_relocation_refuses_same_name_candidates(observed, monkeypatch):
    _, _, _, element, _ = observed
    monkeypatch.setattr(elements, "search", lambda *args, **kwargs: ([make(name=element.name, runtime_id=(91,)), make(name=element.name, runtime_id=(92,))], 2))
    with pytest.raises(elements.AmbiguousTarget):
        elements.relocate(element)


def test_registry_identity_includes_pid_and_window():
    registry = elements.ElementRegistry()
    a = registry.register(make(pid=1, window_handle=100, runtime_id=(1, 2)))
    b = registry.register(make(pid=2, window_handle=200, runtime_id=(1, 2)))
    assert a != b


def test_visual_mapping_handles_negative_monitor_and_scaled_image():
    space = ScreenshotSpace(100, 2, (-1920, -100, 0, 980), (960, 540))
    assert space.point(480, 270, live_handle=100, live_pid=2, live_rect=space.rect) == (-960, 440)
    with pytest.raises(ValueError, match="TARGET_STALE"):
        space.point(480, 270, live_handle=101, live_pid=2, live_rect=space.rect)
    with pytest.raises(ValueError):
        space.point(960, 10, live_handle=100, live_pid=2, live_rect=space.rect)


def test_cancelled_input_allows_cleanup_but_no_keydown(monkeypatch):
    from windows_mcp.desktop import native_input
    from windows_mcp.engine.sessions import _bound

    session = SessionState()
    session.cancelled.set()
    sent = []
    monkeypatch.setattr(native_input._user32, "SendInput", lambda count, *args: sent.append(count) or count)
    token = _bound.set(session)
    try:
        with pytest.raises(native_input.InputBlockedError, match="CANCELLED"):
            native_input._send([native_input._key_event(65)])
        native_input._send([native_input._key_event(65, up=True)])
    finally:
        _bound.reset(token)
    assert sent == [1]


async def test_cancelled_queued_tool_is_rechecked_before_running():
    import asyncio
    import threading
    from windows_mcp.infrastructure import with_analytics
    from windows_mcp.runtime import run_on_desktop
    from windows_mcp.engine.sessions import _bound

    entered, release = threading.Event(), threading.Event()
    visited = []

    def occupy():
        entered.set()
        release.wait(3)

    @with_analytics(None, "queued-probe")
    def action():
        visited.append(True)

    blocker = asyncio.create_task(run_on_desktop(occupy))
    assert await asyncio.to_thread(entered.wait, 1)
    state = SessionState()
    token = _bound.set(state)
    try:
        queued = asyncio.create_task(action())
        await asyncio.sleep(0.03)
        state.cancelled.set()
        release.set()
        with pytest.raises(RuntimeError, match="CANCELLED"):
            await queued
    finally:
        _bound.reset(token)
        release.set()
        await blocker
    assert visited == []


def test_legacy_registry_and_snapshot_are_isolated_by_client():
    from windows_mcp.desktop.service import Desktop

    desktop = Desktop.__new__(Desktop)
    runtime = EngineRuntime()
    with runtime.bind("A"):
        desktop.desktop_state = "A snapshot"
        registry_a = desktop.element_registry
    with runtime.bind("B"):
        assert desktop.desktop_state is None
        assert desktop.element_registry is not registry_a
        desktop.desktop_state = "B snapshot"
    with runtime.bind("A"):
        assert desktop.desktop_state == "A snapshot" and desktop.element_registry is registry_a
