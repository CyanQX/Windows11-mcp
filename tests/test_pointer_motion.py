"""Deterministic local-controller tests with simulated input, clock, targets and handoff."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from tests.test_elements import make
from windows_mcp.desktop import elements, native_input as ni, pointer_motion as pm
from windows_mcp.desktop.pointer_monitor import GestureToken
from windows_mcp.engine.dispatch import trace_dispatch, record_native
from windows_mcp.engine.sessions import SessionState, _bound
from windows_mcp.infrastructure.config import MotionSettings, WindowsMCPConfig, load_config, write_config


@pytest.fixture
def simulation(monkeypatch):
    sim = SimpleNamespace(now=100.0, point=(0, 0), events=[], on_sleep=lambda seconds: None,
                          token=None, session=SessionState())
    screen = ni.VirtualScreen(-500, -100, 2500, 1600)

    def sleep(seconds):
        sim.now += seconds
        sim.on_sleep(seconds)

    def arm(window=0):
        sim.token = GestureToken(100, window)
        return sim.token

    def send(batch):
        releases = all(event.type == ni.INPUT_MOUSE and event.u.mi.dwFlags in
                       {ni.MOUSEEVENTF_LEFTUP, ni.MOUSEEVENTF_RIGHTUP, ni.MOUSEEVENTF_MIDDLEUP} for event in batch)
        if not releases:
            pm.check_handoff()
        record_native(batch, len(batch))
        for event in batch:
            if event.type == ni.INPUT_MOUSE:
                flags = event.u.mi.dwFlags
                if flags & ni.MOUSEEVENTF_MOVE:
                    sim.point = (screen.left + int(event.u.mi.dx * screen.width / 65536),
                                 screen.top + int(event.u.mi.dy * screen.height / 65536))
                    if sim.token is not None:
                        sim.token.last_own_point = sim.point
                sim.events.append((flags, sim.point, sim.now))

    monkeypatch.setattr(pm.time, "monotonic", lambda: sim.now)
    monkeypatch.setattr(ni.time, "sleep", sleep)
    monkeypatch.setattr(ni, "cursor_position", lambda: sim.point)
    monkeypatch.setattr(ni, "virtual_screen", lambda: screen)
    monkeypatch.setattr(ni, "_send", send)
    monkeypatch.setattr(pm, "_check_point", lambda *args: None)
    monkeypatch.setattr(pm.monitor, "arm", arm)
    monkeypatch.setattr(pm.monitor, "disarm", lambda token: None)
    monkeypatch.setattr(pm.monitor, "healthy", lambda: True)
    monkeypatch.setattr(pm.monitor, "window_events", True)
    token = _bound.set(sim.session)
    try:
        with pm.bind_motion(MotionSettings()):
            yield sim
    finally:
        _bound.reset(token)


def downs(sim):
    return [point for flags, point, stamp in sim.events if flags & ni.MOUSEEVENTF_LEFTDOWN]


def test_duration_adapts_to_distance_and_target_width():
    assert pm.travel_seconds(800, 10) > pm.travel_seconds(100, 10)
    assert pm.travel_seconds(800, 10) > pm.travel_seconds(800, 100)
    assert 0.12 <= pm.travel_seconds(1, 100) <= pm.travel_seconds(1e9, 1) <= 0.8
    assert pm.ease(0) == 0 and pm.ease(1) == 1


def test_natural_click_moves_real_simulated_cursor_and_holds_then_releases(simulation):
    sim = simulation
    with trace_dispatch() as trace:
        final = ni.click(500, 300)
    assert final == sim.point == (500, 300)
    assert downs(sim) == [(500, 300)]
    down = next(stamp for flags, point, stamp in sim.events if flags == ni.MOUSEEVENTF_LEFTDOWN)
    up = next(stamp for flags, point, stamp in sim.events if flags == ni.MOUSEEVENTF_LEFTUP)
    assert up - down >= 0.049
    assert trace.snapshot()["motion"]["samples"] > 10
    assert trace.snapshot()["motion"]["stop_reason"] is None


def test_input_delivery_lag_is_not_mistaken_for_target_movement(simulation, monkeypatch):
    sim = simulation
    original_send = ni._send
    pending = []

    def delayed(batch):
        before = sim.point
        original_send(batch)
        if any(event.u.mi.dwFlags & ni.MOUSEEVENTF_MOVE for event in batch):
            pending.append((sim.now + 0.04, sim.point))
            sim.point = before

    def deliver(seconds):
        while pending and pending[0][0] <= sim.now:
            _, sim.point = pending.pop(0)

    monkeypatch.setattr(ni, "_send", delayed)
    sim.on_sleep = deliver
    with trace_dispatch() as trace:
        assert ni.click(500, 300) == (500, 300)
    assert trace.snapshot()["motion"]["replans"] == 0
    assert downs(sim) == [(500, 300)]


def test_already_over_target_click_needs_no_artificial_movement(simulation):
    sim = simulation
    sim.point = (100, 100)
    with trace_dispatch() as trace:
        ni.click(100, 100)
    assert trace.snapshot()["pointer_moves"] == 0
    assert trace.snapshot()["button_downs"] == trace.snapshot()["button_ups"] == 1


def test_target_moving_once_is_followed_without_a_second_model_call(simulation):
    sim = simulation

    class Target:
        def motion_target(self, x, y, *, force=False):
            return ((650, 400) if sim.now >= 100.15 else (500, 300), 16)

        def __call__(self, x, y):
            assert (x, y) == (650, 400)

    with trace_dispatch() as trace:
        assert ni.click(500, 300, before_press=Target()) == (650, 400)
    assert downs(sim) == [(650, 400)]
    assert trace.snapshot()["motion"]["replans"] >= 1


def test_continuously_moving_target_aborts_before_any_press(simulation):
    sim = simulation

    class Target:
        def motion_target(self, x, y, *, force=False):
            return ((500 + int((sim.now - 100) / 0.05) * 15, 300), 16)

        def __call__(self, x, y):
            pytest.fail("unstable target must not be pressed")

    with pytest.raises(ni.InputBlockedError, match="three replans"):
        ni.click(500, 300, before_press=Target())
    assert downs(sim) == []


def test_external_input_after_down_cancels_session_but_releases_our_button(simulation):
    sim = simulation

    def intervene(seconds):
        if downs(sim):
            sim.token.input(0, 101)

    sim.on_sleep = intervene
    with trace_dispatch() as trace, pytest.raises(RuntimeError, match="USER_INTERRUPTED"):
        ni.click(500, 300)
    assert sim.events[-1][0] == ni.MOUSEEVENTF_LEFTUP
    assert sim.session.cancelled.is_set() and sim.session.pointer_interruption == "external_mouse_input"
    assert trace.snapshot()["motion"]["stop_reason"] == "external_mouse_input"


def test_hover_waits_without_pressing(simulation):
    sim = simulation
    ni.click(100, 100, count=0)
    arrived = next(stamp for flags, point, stamp in sim.events if point == (100, 100))
    assert sim.now - arrived >= 0.4
    assert downs(sim) == []


def test_hover_restarts_dwell_when_its_pinned_target_moves(simulation):
    sim = simulation

    class Target:
        def motion_target(self, x, y, *, force=False):
            return ((200, 100) if sim.now >= 100.45 else (100, 100), 30)

        def __call__(self, x, y):
            assert (x, y) == (200, 100)

    assert ni.click(100, 100, count=0, before_press=Target()) == (200, 100)
    arrived = next(stamp for flags, point, stamp in sim.events if point == (200, 100))
    assert sim.now - arrived >= 0.4
    assert downs(sim) == []


def test_external_cursor_reposition_without_hook_event_pauses_instead_of_fighting(simulation):
    sim = simulation

    def reposition(seconds):
        if sim.point == (500, 300):
            sim.point = (550, 350)

    sim.on_sleep = reposition
    with pytest.raises(RuntimeError, match="USER_INTERRUPTED"):
        ni.click(500, 300)
    assert sim.session.pointer_interruption == "external_cursor_reposition" and downs(sim) == []


def test_drag_cleanup_when_destination_changes(simulation):
    sim = simulation

    def vanished(*point):
        raise elements.StaleTarget("drop target vanished")

    with pytest.raises(elements.StaleTarget):
        ni.drag((100, 100), (400, 200), duration=0.2, before_release=vanished)
    assert sim.events[-1][0] == ni.MOUSEEVENTF_LEFTUP
    assert downs(sim) == [(100, 100)]


def test_our_tag_and_queued_old_events_do_not_trigger_takeover():
    token = GestureToken(100)
    token.input(ni.EXTRA_INFO, 120)
    token.input(0, 99)
    assert not token.interrupted.is_set()
    token.input(0, 101, keyboard=True)
    assert token.interrupted.is_set() and token.reason == "external_keyboard_input"


def test_pinned_target_tracks_bounds_but_never_follows_replacement(monkeypatch):
    original = make(rect=(100, 100, 140, 140))
    fresh = make(rect=(200, 200, 240, 240))
    target = elements.pointer_guard(original)
    monkeypatch.setattr(elements, "refresh", lambda element: fresh)
    assert target.motion_target(120, 120, force=True)[0] == (220, 220)
    fresh.runtime_id = (999,)
    with pytest.raises(elements.StaleTarget):
        target.motion_target(120, 120, force=True)


@pytest.mark.parametrize("text", [
    '[interaction]\npointer_motion="jitter"', '[interaction]\nhover_ms=2001',
    '[interaction]\nhover_ms=true', '[interaction]\nhuman_takeover="yes"',
])
def test_invalid_motion_config_is_rejected(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_motion_config_roundtrip(tmp_path):
    cfg = WindowsMCPConfig()
    cfg.interaction.pointer_motion = "legacy"
    cfg.interaction.hover_ms = 700
    cfg.interaction.human_takeover = False
    path = tmp_path / "config.toml"
    write_config(cfg, path)
    assert load_config(path).interaction == cfg.interaction


@pytest.mark.parametrize(("args", "env", "expected"), [
    ([], {}, "legacy"),
    ([], {"WINDOWS_MCP_POINTER_MOTION": "natural"}, "natural"),
    (["--pointer-motion", "legacy"], {"WINDOWS_MCP_POINTER_MOTION": "natural"}, "legacy"),
])
def test_motion_cli_precedence(tmp_path, monkeypatch, args, env, expected):
    from windows_mcp import __main__ as cli
    from windows_mcp.engine import sessions

    monkeypatch.setattr(sessions, "_runtime", None)
    monkeypatch.setattr(cli, "_run_server", lambda **kwargs: None)
    path = tmp_path / "config.toml"
    path.write_text('[interaction]\npointer_motion="legacy"', encoding="utf-8")
    result = CliRunner().invoke(cli.main, ["serve", "--config", str(path), *args], env=env)
    assert result.exit_code == 0, result.output
    assert sessions.get_runtime().motion.style == expected


def test_disabling_handoff_does_not_require_input_hooks(simulation, monkeypatch):
    monkeypatch.setattr(pm.monitor, "arm", lambda *args: pytest.fail("disabled handoff must not install hooks"))
    with pm.bind_motion(MotionSettings(human_takeover=False)):
        assert ni.click(100, 100) == (100, 100)


def test_unavailable_monitor_fails_before_pointer_input(simulation, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("POINTER_MONITOR_UNAVAILABLE")

    monkeypatch.setattr(pm.monitor, "arm", unavailable)
    with pytest.raises(RuntimeError, match="POINTER_MONITOR_UNAVAILABLE"):
        ni.click(100, 100)
    assert simulation.events == []
