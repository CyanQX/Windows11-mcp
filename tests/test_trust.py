"""The operator trust grant: minted locally, signed, revocable and never wider than R2."""

import json

import pytest

from windows_mcp.infrastructure.approvals import ApprovalStore
from windows_mcp.infrastructure.policy import PolicyBlocked, PolicyGate
from windows_mcp.infrastructure.trust import TrustStore


def test_session_grant_allows_and_expires(tmp_path, monkeypatch):
    store = TrustStore(tmp_path, session_ttl=60)
    assert not store.allows()
    payload = store.grant("session")
    assert store.allows()
    assert payload["expires_at"] is not None
    import windows_mcp.infrastructure.trust as module

    monkeypatch.setattr(module.time, "time", lambda: payload["expires_at"] + 1)
    assert not store.allows()


def test_forever_grant_survives_restart_and_revokes(tmp_path):
    store = TrustStore(tmp_path)
    store.grant("forever")
    assert TrustStore(tmp_path).allows()
    store.revoke()
    assert not TrustStore(tmp_path).allows()


def test_tampered_trust_record_is_ignored(tmp_path):
    store = TrustStore(tmp_path)
    store.grant("session")
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record["payload"]["mode"] = "forever"
    record["payload"]["expires_at"] = None
    store.path.write_text(json.dumps(record), encoding="utf-8")
    assert not store.allows()


def test_policy_gate_skips_r2_approval_only_while_trusted(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gate = PolicyGate(roots=[workspace], approvals=ApprovalStore(tmp_path / "authority"))
    with pytest.raises(PolicyBlocked):
        gate.authorize("session", "Click", {"loc": [100, 200]})

    gate.trust.grant("forever")
    assert gate.authorize("session", "Click", {"loc": [100, 200]}) == 2
    assert gate.authorize("session", "Type", {"text": "hi"}) == 2

    with pytest.raises(PolicyBlocked):
        gate.authorize("session", "PowerShell", {"command": "Get-Process"})

    gate.trust.revoke()
    with pytest.raises(PolicyBlocked):
        gate.authorize("session", "Click", {"loc": [100, 200]})


def test_trust_file_cannot_be_written_through_the_tool_workspace(tmp_path):
    gate = PolicyGate(roots=[tmp_path / "workspace"], approvals=ApprovalStore(tmp_path / "authority"))
    gate.trust.grant("forever")
    with pytest.raises(PolicyBlocked):
        gate.check_path(gate.trust.path)
