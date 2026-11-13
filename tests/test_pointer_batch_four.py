"""Fault-injection coverage for pointer routing, final target checks and dispatch evidence."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from tests.test_elements import make
from windows_mcp.desktop import elements as el, native_input as ni
from windows_mcp.engine.action_router import execute
from windows_mcp.engine.dispatch import record_uia, trace_dispatch
from windows_mcp.engine.sessions import EngineRuntime, SessionState, _bound
from windows_mcp.infrastructure.config import WindowsMCPConfig, load_config, write_config
from windows_mcp.infrastructure.policy import PolicyGate


@pytest.mark.parametrize("mode", ["semantic", "pointer"])
def test_interaction_config_roundtrip(tmp_path, mode):
    cfg = WindowsMCPConfig()
    cfg.interaction.mode = mode
    path = tmp_path / "config.toml"
    write_config(cfg, path)
    assert load_config(path).interaction == cfg.interaction


@pytest.mark.parametrize("text", [
    '[interaction]\nmode="random"', '[interaction]\nmode=true',
    'interaction="pointer"', '[interaction]\nunknown=true',
])
def test_invalid_interaction_config_is_rejected(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="interaction"):
        load_config(path)


@pytest.mark.parametrize(("arguments", "environment", "wanted"), [
    ([], {}, "pointer"),
    (["--interaction-mode", "semantic"], {}, "semantic"),
    ([], {"WINDOWS_MCP_INTERACTION_MODE": "semantic"}, "semantic"),
    (["--interaction-mode", "pointer"], {"WINDOWS_MCP_INTERACTION_MODE": "semantic"}, "pointer"),
])
def test_cli_interaction_precedence(tmp_path, monkeypatch, arguments, environment, wanted):
    from windows_mcp import __main__ as cli
    from windows_mcp.engine import sessions

    monkeypatch.setattr(sessions, "_runtime", None)
    path = tmp_path / "config.toml"
    path.write_text('[interaction]\nmode="pointer"', encoding="utf-8")
    monkeypatch.setattr(cli, "_run_server", lambda **kwargs: None)
    result = CliRunner().invoke(cli.main, ["serve", "--config", str(path), *arguments], env=environment)
    assert result.exit_code == 0, result.output
    assert sessions.get_runtime().interaction_mode == wanted


@pytest.fixture
def routing(monkeypatch, tmp_path):
    element = make(capabilities=frozenset({"invoke", "value"}))
    runtime = EngineRuntime(PolicyGate(roots=[tmp_path]), interaction_mode="pointer")
    session = SessionState()
    from windows_mcp.engine import action_router

    monkeypatch.setattr(action_router, "resolve", lambda *args: element)
    return runtime, session, element


@pytest.mark.parametrize(("server_mode", "action_mode", "action", "wanted"), [
    ("pointer", "auto", "click", "input"),
    ("pointer", "auto", "hover", "input"),
    ("pointer", "semantic", "click", "uia"),
    ("semantic", "physical", "click", "input"),
    ("semantic", "auto", "click", "uia"),
    ("pointer", "auto", "set_value", "uia"),
])
def test_routing_respects_server_policy_and_explicit_mode(routing, monkeypatch, server_mode, action_mode, action, wanted):
    runtime, session, element = routing
    runtime.interaction_mode = server_mode
    methods = []

    def act(*args, via, **kwargs):
        methods.append(via)
        return el.ActOutcome("no dispatch in test", True, "", [], element)

    monkeypatch.setattr(el, "act", act)
    execute(None, runtime, session, target="test", action=action, mode=action_mode)
    assert methods == [wanted]


def test_failed_precondition_does_not_claim_dispatch(routing, monkeypatch):
    runtime, session, _ = routing

    def blocked(*args, **kwargs):
        raise el.OccludedError("covered before movement")

    monkeypatch.setattr(el, "act", blocked)
    result = execute(None, runtime, session, target="test", action="click")
    assert result["status"] == "FAILED"
    assert not result["input_dispatched"] and not result["precondition_passed"]
    assert result["dispatch"]["native_events"] == result["dispatch"]["uia_calls"] == 0


def test_provider_call_followed_by_error_remains_unverified(routing, monkeypatch):
    runtime, session, _ = routing

    def invoked(*args, **kwargs):
        record_uia()
        raise el.StaleTarget("dialog replaced the original control after Invoke")

    monkeypatch.setattr(el, "act", invoked)
    result = execute(None, runtime, session, target="test", action="click", mode="semantic")
    assert result["status"] == "DISPATCHED_UNVERIFIED"
    assert result["input_dispatched"] and not result["retryable"]
    assert result["dispatch"]["uia_calls"] == 1


def test_verified_noop_does_not_claim_input(routing, monkeypatch):
    runtime, session, element = routing
    monkeypatch.setattr(el, "act", lambda *args, **kwargs: el.ActOutcome("no action needed", True, "already on", [], element))
    result = execute(None, runtime, session, target="test", action="toggle", value="on")
    assert result["status"] == "VERIFIED" and not result["input_dispatched"]


def test_native_trace_counts_accepted_events_and_partial_click_cleanup(monkeypatch):
    monkeypatch.setattr(ni, "cursor_position", lambda: (50, 50))
    monkeypatch.setattr(ni, "move_to", lambda *args, **kwargs: (50, 50))
    batches = []

    def partial(count, array, size):
        batches.append([array[index].u.mi.dwFlags for index in range(count)])
        return 1  # DOWN accepted, UP missing; cleanup must send a separate UP.

    monkeypatch.setattr(ni._user32, "SendInput", partial)
    with trace_dispatch() as trace, pytest.raises(ni.InputBlockedError):
        ni.click(50, 50)
    assert batches == [[ni.MOUSEEVENTF_LEFTDOWN, ni.MOUSEEVENTF_LEFTUP], [ni.MOUSEEVENTF_LEFTUP]]
    evidence = trace.snapshot()
    assert evidence["native_events"] == 2
    assert evidence["button_downs"] == evidence["button_ups"] == 1
    assert evidence["partial_native"]


@pytest.mark.parametrize("change", ["moved", "replaced", "disabled", "covered"])
def test_control_is_rechecked_after_movement_before_press(monkeypatch, change):
    original = make(rect=(0, 0, 100, 100))
    fresh = make(rect=(100, 100, 200, 200) if change == "moved" else original.rect,
                 runtime_id=(99,) if change == "replaced" else original.runtime_id,
                 enabled=change != "disabled")
    current = [original]
    monkeypatch.setattr(el, "refresh", lambda element: current[0])
    monkeypatch.setattr(el, "hits", lambda *args: change != "covered")
    monkeypatch.setattr(ni, "cursor_position", lambda: (50, 50))

    def move(*args, **kwargs):
        current[0] = fresh
        return (50, 50)

    monkeypatch.setattr(ni, "move_to", move)
    sent = []
    monkeypatch.setattr(ni, "_send", lambda batch: sent.extend(batch))
    with pytest.raises(el.ElementError):
        ni.click(50, 50, before_press=el.pointer_guard(original))
    assert sent == []


def test_recheck_runs_between_double_click_presses(monkeypatch):
    target = make(rect=(0, 0, 100, 100))
    monkeypatch.setattr(el, "refresh", lambda element: target)
    monkeypatch.setattr(el, "hits", lambda *args: True)
    monkeypatch.setattr(ni, "cursor_position", lambda: (50, 50))
    monkeypatch.setattr(ni, "move_to", lambda *args, **kwargs: (50, 50))
    monkeypatch.setattr(ni.time, "sleep", lambda seconds: None)
    sent = []

    def send(batch):
        sent.extend(batch)
        target.enabled = False

    monkeypatch.setattr(ni, "_send", send)
    with pytest.raises(el.NotEnabledError):
        ni.click(50, 50, count=2, before_press=el.pointer_guard(target))
    assert len(sent) == 2  # Only the first DOWN/UP pair, never a second press.


def test_drag_unreachable_start_never_presses(monkeypatch):
    monkeypatch.setattr(ni, "move_to", lambda *args, **kwargs: (0, 0))
    sent = []
    monkeypatch.setattr(ni, "_send", lambda batch: sent.extend(batch))
    with pytest.raises(ni.InputBlockedError, match="drag start"):
        ni.drag((10, 10), (50, 50))
    assert sent == []


def test_cancellation_after_drag_press_always_releases(monkeypatch):
    session = SessionState()
    token = _bound.set(session)
    monkeypatch.setattr(ni, "move_to", lambda *args, **kwargs: (10, 10))
    monkeypatch.setattr(ni, "cursor_position", lambda: (10, 10))
    sent = []

    def send(count, array, size):
        sent.extend(array[index].u.mi.dwFlags for index in range(count))
        if ni.MOUSEEVENTF_LEFTDOWN in sent:
            session.cancel()
        return count

    monkeypatch.setattr(ni._user32, "SendInput", send)
    try:
        with pytest.raises(ni.InputBlockedError, match="CANCELLED"):
            ni.drag((10, 10), (50, 50))
        assert sent == [ni.MOUSEEVENTF_LEFTDOWN, ni.MOUSEEVENTF_LEFTUP]
    finally:
        _bound.reset(token)


def test_cancelled_cursor_correction_cannot_snap_pointer(monkeypatch):
    session = SessionState()
    token = _bound.set(session)
    monkeypatch.setattr(ni, "virtual_screen", lambda: ni.VirtualScreen(0, 0, 100, 100))
    monkeypatch.setattr(ni, "cursor_position", lambda: (49, 50))
    monkeypatch.setattr(ni, "_send", lambda batch: session.cancel())
    try:
        with pytest.raises(ni.InputBlockedError, match="CANCELLED"):
            ni.move_to(50, 50)
    finally:
        _bound.reset(token)


def test_dispatch_trace_does_not_leak_between_actions():
    with trace_dispatch() as first:
        record_uia()
    with trace_dispatch() as second:
        pass
    assert first.snapshot()["uia_calls"] == 1 and second.snapshot()["uia_calls"] == 0


def test_partial_shortcut_releases_only_accepted_held_keys(monkeypatch):
    batches = []

    def partial(count, array, size):
        batches.append([(array[index].u.ki.wVk, bool(array[index].u.ki.dwFlags & ni.KEYEVENTF_KEYUP))
                        for index in range(count)])
        return 2 if len(batches) == 1 else count

    monkeypatch.setattr(ni._user32, "SendInput", partial)
    with pytest.raises(ni.InputBlockedError):
        ni.press_keys("ctrl+shift+s")
    assert batches[1] == [(ni.VK_SHIFT, True), (ni.VK_CONTROL, True)]
