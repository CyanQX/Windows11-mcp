"""Scopes come from the server, and are rechecked before mocked UIA/SendInput effects."""

import json
from types import SimpleNamespace

import pytest

from tests import test_policy_enforcement
from tests.test_elements import make
from windows_mcp.desktop import elements, native_input
from windows_mcp.infrastructure import authorization as auth
from windows_mcp.infrastructure.approvals import sign_record
from windows_mcp.infrastructure.policy import PolicyBlocked
from windows_mcp.infrastructure.trust import TrustStore


APP = "c:\\windows\\system32\\notepad.exe"
SCOPE = {"application": APP, "window": 123, "pid": 456, "process_started": 100.0, "sensitive": False}
secured = test_policy_enforcement.secured


def test_trust_is_limited_by_session_application_tool_action_and_expiry(tmp_path, monkeypatch):
    store = TrustStore(tmp_path)
    grant = store.grant(session_id="owner", applications=[APP], tools=["Execute"], actions=["set_value"])
    assert store.allows("owner", APP.upper(), "Execute", "set_value")
    for args in [("other", APP, "Execute", "set_value"), ("owner", "other.exe", "Execute", "set_value"),
                 ("owner", APP, "Click", "click"), ("owner", APP, "Execute", "invoke")]:
        assert not store.allows(*args)
    monkeypatch.setattr("windows_mcp.infrastructure.trust.time.time", lambda: grant["expires_at"] + 1)
    assert not store.allows("owner", APP, "Execute", "set_value")


def test_legacy_unscoped_grant_requires_new_operator_confirmation(tmp_path):
    store = TrustStore(tmp_path)
    store.path.write_text(json.dumps(sign_record(tmp_path, {"schema": 1, "mode": "forever", "expires_at": None})))
    assert store.status() is None
    assert not store.allows("owner", APP, "Click", "click")


@pytest.mark.parametrize("shortcut", ["win+r", "windows+r", "win+x", "win+s", "win", "ctrl+shift+esc", "ctrl+esc", "ctrl+k win+r"])
def test_system_entry_shortcuts_are_r3_even_with_scoped_trust(secured, shortcut):
    runtime, _, _ = secured
    session = runtime.session("local")
    runtime.policy.trust.grant("forever", session_id=session.session_id, applications=[APP])
    assert runtime.policy.risk("Shortcut", {"shortcut": shortcut}, SCOPE) == 3
    with pytest.raises(PolicyBlocked, match="R3"):
        runtime.policy.authorize(session.session_id, "Shortcut", {"shortcut": shortcut}, scope=SCOPE)


@pytest.mark.parametrize("tool,args", [("Type", {"text": "command"}), ("Execute", {"action": "set_value"}), ("Act", {"action": "invoke"})])
def test_sensitive_targets_promote_both_physical_and_semantic_actions_to_r3(secured, tool, args):
    runtime, _, _ = secured
    sensitive = {**SCOPE, "sensitive": True}
    runtime.policy.trust.grant("forever", session_id="owner", applications=[APP])
    assert runtime.policy.risk(tool, args, sensitive) == 3
    with pytest.raises(PolicyBlocked, match="R3"):
        runtime.policy.authorize("owner", tool, args, scope=sensitive)


def test_one_use_approval_is_bound_to_window_process_lifetime(secured):
    runtime, _, _ = secured
    args = {"text": "owned"}
    request = runtime.policy.approvals.request("owner", "Type", args, 2, "fixture", authorization_scope=SCOPE)
    runtime.policy.approvals.approve(request["request_id"])
    for changed in [{**SCOPE, "window": 999}, {**SCOPE, "pid": 999}, {**SCOPE, "process_started": 999}]:
        with pytest.raises(PolicyBlocked):
            runtime.policy.authorize("owner", "Type", args, scope=changed)
    assert runtime.policy.authorize("owner", "Type", args, scope=SCOPE) == 2
    with pytest.raises(PolicyBlocked):
        runtime.policy.authorize("owner", "Type", args, scope=SCOPE)


def _dispatch(runtime, monkeypatch):
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.operator_review_active", lambda: False)
    monkeypatch.setattr(auth, "window_scope", lambda hwnd: dict(SCOPE))
    session = runtime.current()
    runtime.policy.trust.grant(session_id=session.session_id, applications=[APP])
    return auth.DispatchAuthorization(runtime.policy, session.session_id, "Type", {"text": "owned"}, SCOPE, 2, True)


def test_focus_drift_is_blocked_before_sendinput_but_cleanup_releases_still_work(secured, monkeypatch):
    runtime, _, _ = secured
    inserted = []
    monkeypatch.setattr(native_input._user32, "SendInput", lambda count, *args: inserted.append(count) or count)
    monkeypatch.setattr(elements, "foreground_root", lambda: 123)
    with runtime.bind("local"):
        dispatch = _dispatch(runtime, monkeypatch)
        with auth.bind_dispatch(dispatch):
            monkeypatch.setattr(auth, "window_scope", lambda hwnd: {**SCOPE, "application": "cmd.exe", "sensitive": True})
            with pytest.raises(PolicyBlocked, match="Sensitive"):
                native_input._send(native_input.text_events("must not execute"))
            assert inserted == []
            runtime.current().cancelled.set()
            native_input._send([native_input._key_event(native_input.VK_CONTROL, up=True)])
            assert inserted == [1]


def test_semantic_guard_runs_before_uia_and_rechecks_revocation(secured, monkeypatch):
    runtime, _, _ = secured
    with runtime.bind("local"):
        dispatch = _dispatch(runtime, monkeypatch)
        with auth.bind_dispatch(dispatch):
            runtime.policy.trust.revoke()
            monkeypatch.setattr(elements, "get_pattern", lambda *args: pytest.fail("No UIA call is allowed after revocation"))
            with pytest.raises(PolicyBlocked, match="revoked"):
                elements.act(SimpleNamespace(), make(window_handle=123), "set_value", "must not execute", via="uia")


async def test_other_sessions_do_not_report_or_inherit_active_trust(secured):
    runtime, mcp, _ = secured
    runtime.policy.trust.grant(session_id="another-client", applications=["native:filesystem"])
    state = await mcp.tools["Session"]()
    assert state["trust_mode"] is None and state["trust_scope"] is None


def test_scoped_trust_cli_displays_and_enforces_the_signed_request_scope(secured, monkeypatch):
    from click.testing import CliRunner
    from windows_mcp import __main__ as cli

    runtime, _, _ = secured
    monkeypatch.setattr(cli, "CONFIG_DIR", runtime.policy.approvals.directory.parent)
    # The CLI uses CONFIG_DIR/approvals; keep this test's authority isolated.
    from windows_mcp.infrastructure.approvals import ApprovalStore

    authority = ApprovalStore(cli.CONFIG_DIR / "approvals")
    request = authority.request("client-A", "Click", {"loc": [1, 2]}, 2, "owned", authorization_scope=SCOPE)
    runner = CliRunner()
    assert runner.invoke(cli.main, ["trust", "--yes"]).exit_code != 0
    result = runner.invoke(cli.main, ["trust", "--request-id", request["request_id"], "--yes"])
    assert result.exit_code == 0 and "client-A" in result.output and APP in result.output
    store = TrustStore(authority.directory)
    assert store.allows("client-A", APP, "Click", "click")
    assert not store.allows("client-B", APP, "Click", "click")


def test_owned_popup_keeps_one_use_scope_but_other_windows_do_not(secured, monkeypatch):
    runtime, _, _ = secured
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.operator_review_active", lambda: False)
    popup = {**SCOPE, "window": 789}
    monkeypatch.setattr(auth, "window_scope", lambda hwnd: dict(SCOPE) if hwnd == 123 else dict(popup))
    monkeypatch.setattr(elements, "_owned_by", lambda hwnd, owner: hwnd == 789 and owner == 123)
    with runtime.bind("local") as session:
        dispatch = auth.DispatchAuthorization(runtime.policy, session.session_id, "Type", {}, SCOPE, 2, False)
        dispatch.check_window(789)
        monkeypatch.setattr(elements, "_owned_by", lambda *args: False)
        with pytest.raises(PolicyBlocked, match="lifetime"):
            dispatch.check_window(789)


def test_multi_application_gesture_needs_every_application_in_trust_scope(secured, monkeypatch):
    runtime, _, _ = secured
    other = {**SCOPE, "application": "other.exe", "window": 789, "pid": 999}
    monkeypatch.setattr(elements, "root_at", lambda x, y: 123 if x < 100 else 789)
    monkeypatch.setattr(auth, "window_scope", lambda hwnd: dict(SCOPE) if hwnd == 123 else dict(other))
    scope = auth.scope_for_call("Move", {"from_loc": [1, 2], "drag_to": [200, 200], "drag": True},
                                desktop=SimpleNamespace(), session=runtime.current())
    assert set(scope["applications"]) == {APP, "other.exe"}
    runtime.policy.trust.grant(session_id="owner", applications=[APP])
    assert not runtime.policy.trusted("owner", "Move", {"drag": True}, scope)
    runtime.policy.trust.grant(session_id="owner", applications=[APP, "other.exe"])
    assert runtime.policy.trusted("owner", "Move", {"drag": True}, scope)
