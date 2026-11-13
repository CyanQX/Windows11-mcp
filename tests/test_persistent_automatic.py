"""Persistent operator consent is independent of ephemeral MCP client identities."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from click.testing import CliRunner

from tests import test_policy_enforcement
from windows_mcp.infrastructure.approvals import ApprovalStore
from windows_mcp.infrastructure.policy import PolicyBlocked, PolicyGate
from windows_mcp.infrastructure.trust import TrustStore
from windows_mcp.infrastructure import authorization

secured = test_policy_enforcement.secured


def test_automatic_survives_time_restart_reconnect_and_application_switch(tmp_path, monkeypatch):
    store = TrustStore(tmp_path / "authority")
    payload = store.grant_automatic(operator="isolated-local-operator")
    assert payload["expires_at"] is None and payload["scope"] == "installation"
    monkeypatch.setattr("windows_mcp.infrastructure.trust.time.time", lambda: payload["granted_at"] + 10 * 365 * 86400)
    restarted = TrustStore(store.directory)
    for client, app in [("client-A", "notepad.exe"), ("reconnected", "browser.exe"), ("client-B", "native:filesystem")]:
        assert restarted.allows(client, app, "Click", "click")
    restarted.revoke()
    assert not store.allows("client-A", "notepad.exe", "Click", "click")


def test_automatic_is_loaded_by_a_fresh_server_process_and_observes_revocation(tmp_path):
    store = TrustStore(tmp_path / "authority")
    script = """
import sys
from pathlib import Path
from windows_mcp.infrastructure.trust import TrustStore
store = TrustStore(Path(sys.argv[1]))
print(store.allows('new-transport', 'new-app.exe', 'Click', 'click'), flush=True)
input()
print(store.allows('new-transport', 'new-app.exe', 'Click', 'click'), flush=True)
"""
    store.grant_automatic()
    with subprocess.Popen([sys.executable, "-u", "-c", script, str(store.directory)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / 'src')}) as child:
        try:
            assert child.stdout.readline().strip() == "True"
            store.revoke()
            output, errors = child.communicate("continue\n", timeout=15)
            assert child.returncode == 0, errors
            assert output.strip() == "False"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


def test_automatic_keeps_capability_switch_and_workspace_limits(tmp_path, monkeypatch):
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.operator_review_active", lambda: False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gate = PolicyGate(roots=[workspace], approvals=ApprovalStore(tmp_path / "authority"))
    gate.trust.grant_automatic()
    assert gate.authorize("reconnected", "Click", {"loc": [1, 2]}, scope={"application": "new.exe"}) == 2
    with pytest.raises(PolicyBlocked, match="R3"):
        gate.authorize("reconnected", "PowerShell", {"command": "Get-Date"})
    gate.allow_r3 = True
    assert gate.authorize("reconnected", "PowerShell", {"command": "Get-Date"}) == 3
    with pytest.raises(PolicyBlocked, match="outside"):
        gate.authorize("reconnected", "NativeAPI", {"operation": "write", "path": str(tmp_path / "outside.txt")})
    gate.roots = [tmp_path]
    with pytest.raises(PolicyBlocked, match="protected"):
        gate.check_path(gate.trust.path)


def test_automatic_network_persists_for_new_sessions_and_new_origins(tmp_path):
    gate = PolicyGate(approvals=ApprovalStore(tmp_path))
    gate.trust.grant_automatic()
    for session, url in [("first", "https://example.com/path"), ("reconnected", "https://different.example.org/")]:
        gate.check_network(session, url)
    with pytest.raises(ValueError):
        gate.check_network("first", "file:///C:/secret.txt")
    gate.trust.revoke()
    with pytest.raises(PolicyBlocked, match="Network origin"):
        gate.check_network("reconnected", "https://different.example.org/")


def test_legacy_scoped_grants_do_not_silently_become_installation_wide(tmp_path):
    store = TrustStore(tmp_path)
    store.grant("forever", session_id="old", applications=["notepad.exe"])
    assert not store.automatic()
    assert not store.allows("new", "other.exe", "Click", "click")


def test_tampering_cannot_enable_persistent_automatic(tmp_path):
    store = TrustStore(tmp_path)
    store.grant("session", session_id="old", applications=["notepad.exe"])
    record = json.loads(store.path.read_text())
    record["payload"].update(schema=3, mode="automatic", scope="installation", expires_at=None, max_risk=3, network=True, tools=["*"])
    store.path.write_text(json.dumps(record))
    assert not store.automatic()


async def test_reconnected_client_uses_actual_gateway_without_opening_approval(secured, monkeypatch):
    runtime, mcp, workspace = secured
    runtime.policy.trust.grant_automatic()
    path = workspace / "automatic.txt"
    path.write_text("before")
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.notify_operator", lambda *_: pytest.fail("Automatic mode opened a manual prompt"))
    monkeypatch.setattr("windows_mcp.vision.native_bridge.capabilities", lambda: {})
    for owner in ["first-client", "reconnected-client", "another-client"]:
        with runtime.bind(owner):
            # Tools bind the transport-derived owner; change the harness's local
            # session to model reconnects without letting the agent choose an id.
            state = runtime.current()
        runtime.sessions["local"] = state
        status = await mcp.tools["Session"]()
        assert status["trust_mode"] == "automatic" and status["trust_scope"]["expires_at"] is None
        args = {"operation": "write", "path": str(path), "content": owner, "overwrite": True}
        response = await mcp.tools["RequestApproval"](tool="NativeAPI", arguments=args, description="owned test overwrite")
        assert response["status"] == "TRUSTED" and not response["approval_required"]
        assert (await mcp.tools["NativeAPI"](**args))["status"] == "VERIFIED"
        assert path.read_text() == owner
    assert runtime.policy.approvals.pending() == []


def test_revocation_is_checked_at_enabled_r3_dispatch_boundary(secured):
    runtime, _, _ = secured
    runtime.policy.allow_r3 = True
    runtime.policy.trust.grant_automatic()
    with runtime.bind("local") as state:
        dispatch = authorization.DispatchAuthorization(runtime.policy, state.session_id, "PowerShell",
            {"command": "Get-Date"}, {"application": "provider:powershell"}, 3, True, state.generation)
        dispatch.check()
        runtime.policy.trust.revoke()
        with pytest.raises(PolicyBlocked, match="revoked"):
            dispatch.check()


def test_old_pending_sheet_is_retired_without_minting_one_use_approval(tmp_path):
    store = ApprovalStore(tmp_path)
    request = store.request("old", "Click", {"loc": [1, 2]}, 2, "old request")
    assert store.dismiss_automatic() == 0
    TrustStore(tmp_path).grant_automatic()
    assert store.dismiss_automatic() == 1 and store.pending() == []
    assert not store.consume("old", "Click", {"loc": [1, 2]}, request["request_id"])


def test_retired_pending_records_are_pruned(tmp_path, monkeypatch):
    store = ApprovalStore(tmp_path)
    request = store.request("old", "Click", {"loc": [1, 2]}, 2, "old request")
    TrustStore(tmp_path).grant_automatic()
    assert store.dismiss_automatic() == 1
    retired = tmp_path / (request["request_id"] + ".automatic")
    assert retired.exists()
    future = retired.stat().st_mtime + store.ttl * 2 + 1
    monkeypatch.setattr("windows_mcp.infrastructure.approvals.time.time", lambda: future)
    store.prune()
    assert not retired.exists()
    assert TrustStore(tmp_path).automatic()


@pytest.mark.parametrize("mode", ["session", "forever", "automatic"])
def test_request_cleanup_never_expires_trust_or_network_grants(tmp_path, monkeypatch, mode):
    from windows_mcp.infrastructure.network import NetworkGrants

    store = ApprovalStore(tmp_path)
    trust = TrustStore(tmp_path)
    if mode == "automatic":
        record = trust.grant_automatic()
    else:
        record = trust.grant(mode, session_id="owner", applications=["notepad.exe"])
    network = NetworkGrants(tmp_path)
    network.grant("owner", ["https://example.com"])
    original_trust, original_network = trust.path.read_bytes(), network.path.read_bytes()
    authority = (tmp_path / "authority.key").read_bytes()
    stale = store.request("expired", "Click", {"loc": [1, 2]}, 2, "will expire")
    future = record["granted_at"] + 3600
    monkeypatch.setattr("windows_mcp.infrastructure.approvals.time.time", lambda: future)
    # Request creation invokes prune just as a new approval in a running server does.
    store.request("new", "Click", {"loc": [3, 4]}, 2, "new request")
    assert not (tmp_path / (stale["request_id"] + ".json")).exists()
    assert trust.path.read_bytes() == original_trust
    assert network.path.read_bytes() == original_network
    assert (tmp_path / "authority.key").read_bytes() == authority
    assert trust.allows("owner", "notepad.exe", "Click", "click")
    assert network.allows("owner", "https://example.com")


def test_frozen_notifier_does_not_open_a_redundant_review(tmp_path, monkeypatch):
    from windows_mcp.infrastructure import operator_ui

    monkeypatch.setattr("windows_mcp.infrastructure.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    TrustStore(tmp_path / "approvals").grant_automatic()
    monkeypatch.setattr(operator_ui.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Redundant approval UI was launched"))
    assert not operator_ui.notify_operator("a" * 32)


def test_operator_cli_can_explicitly_save_and_revoke_automatic(tmp_path, monkeypatch):
    from windows_mcp import __main__ as cli

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    runner = CliRunner()
    store = TrustStore(tmp_path / "approvals")
    assert runner.invoke(cli.main, ["trust", "--mode", "automatic"], input="n\n").exit_code != 0
    assert not store.automatic()
    assert runner.invoke(cli.main, ["trust", "--mode", "automatic", "--yes"]).exit_code == 0
    assert store.automatic()
    assert runner.invoke(cli.main, ["trust", "--revoke", "--yes"]).exit_code == 0
    assert not store.automatic()
