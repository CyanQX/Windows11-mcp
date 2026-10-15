"""Private settings, report redaction and local GUI approval invariants."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from installer.diagnostics import EventLog, Redactor
from installer.models import ManagerError
from installer.settings import SettingsService
from windows_mcp.infrastructure.approvals import ApprovalStore
from windows_mcp.infrastructure.operator_ui import notify_operator


def test_settings_roundtrip_and_unsupported_environment(tmp_path):
    home = tmp_path / "home"
    settings = SettingsService(home, env={"LOCALAPPDATA": str(home / "AppData")})
    value = {**settings.value, "theme": "dark", "health_timeout": 30}
    settings.save(value)
    assert SettingsService(home, env={"LOCALAPPDATA": str(home / "AppData")}).value["theme"] == "dark"
    value["environment_overrides"] = {"WINDOWS_MCP_UNRESTRICTED": "1"}
    with pytest.raises(ManagerError, match="INVALID_ENVIRONMENT"):
        settings.save(value)


def test_settings_no_silent_migration(tmp_path):
    home = tmp_path / "home"
    settings = SettingsService(home, env={"LOCALAPPDATA": str(home / "AppData")})
    receipts = home / "AppData" / "WindowsMCP" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "codex.json").write_text("{}")
    with pytest.raises(ManagerError, match="PATH_HAS_MANAGED_TARGETS"):
        settings.save({**settings.value, "data_dir": str(home / "new")})
    assert (receipts / "codex.json").is_file()


def test_report_redacts_private_values(tmp_path):
    home = tmp_path / "Alice"
    redactor = Redactor(home)
    value = redactor.clean({"path": str(home / "private.txt"), "Authorization": "Bearer secret", "api_key": "abc", "text": "https://user:password@example.com/?token=secret Bearer private-value sk-abcdefghijkl"})
    text = json.dumps(value)
    assert str(home).replace("\\", "\\\\") not in text
    for secret in ("secret", "password", "private-value", "sk-abcdefghijkl"):
        assert secret not in text
    log = EventLog(tmp_path / "data", home, "1.2.1")
    log.emit("test", str(home / "private.txt"), token="NEVER_EXPORT")
    report = json.dumps(log.report({}))
    assert "NEVER_EXPORT" not in report and "Alice" not in report


def test_operator_rejection_does_not_grant(tmp_path):
    store = ApprovalStore(tmp_path / "approvals")
    row = store.request("s", "Files", {"operation": "write", "path": "example.txt"}, 2, "fixture")
    assert not store.consume("s", "Files", {"operation": "write", "path": "example.txt"})
    assert store.pending()[0]["approved"] is False
    assert store.operator_arguments(row["request_id"]) == {"operation": "write", "path": "example.txt"}
    store.reject(row["request_id"])
    assert not store.pending()
    with pytest.raises(FileNotFoundError):
        store.approve(row["request_id"])


def test_local_approval_consumed_once_across_instances(tmp_path):
    root = tmp_path / "approvals"
    first = ApprovalStore(root)
    row = first.request("s", "Files", {"operation": "write"}, 2, "fixture")
    first.approve(row["request_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ApprovalStore(root).consume("s", "Files", {"operation": "write"}, row["request_id"]), range(2)))
    assert results.count(True) == 1


def test_notification_never_auto_approves(tmp_path, monkeypatch):
    import sys
    import subprocess
    store = ApprovalStore(tmp_path / "approvals")
    row = store.request("s", "Files", {"operation": "write"}, 2, "fixture")
    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: calls.append(args))
    assert notify_operator(row["request_id"])
    assert calls[0][1:] == ["--review-request", row["request_id"]]
    assert store.read(row["request_id"])["approved"] is False
    assert not store.consume("s", "Files", {"operation": "write"})


def test_mcp_cannot_click_human_review_even_with_old_grant(tmp_path, monkeypatch):
    from windows_mcp.infrastructure.policy import PolicyGate, PolicyBlocked
    from windows_mcp.infrastructure import operator_ui
    store = ApprovalStore(tmp_path / "approvals")
    args = {"x": 10, "y": 20}
    row = store.request("s", "Click", args, 2, "fixture")
    store.approve(row["request_id"])
    monkeypatch.setattr(operator_ui, "operator_review_active", lambda: True)
    gate = PolicyGate(approvals=store)
    with pytest.raises(PolicyBlocked, match="human approval window"):
        gate.authorize("s", "Click", args)
    assert store.read(row["request_id"])["approved"]  # blocked without consuming
