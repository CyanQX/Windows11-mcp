"""Legacy migration exercises real owned files in fake homes, never live clients."""
import json
import shutil

import pytest

from installer.bundle import create
from installer.migration import prepare, commit, rollback, SetupJournal
from installer.models import ManagerError, IntegrationStatus
from tests.installer_helpers import make_service


def fixture(tmp_path, old_version):
    service = make_service(tmp_path / "fixture")
    service.backend.version = service.manifest["version"] = old_version
    service.install_targets(["codex", "claude"])
    old = service.backend.executable.read_bytes()
    root = tmp_path / "new-payload"
    root.mkdir()
    executable = root / "Windows-MCP.exe"
    executable.write_bytes(b"MZ Actilo replacement")
    contents = "_runtime-" + "b" * 20
    (root / contents).mkdir()
    (root / contents / "python312.dll").write_bytes(b"isolated dependency")
    create(root, contents, "1.9.10")
    service.backend.source_executable = executable
    service.backend.version = service.manifest["version"] = service.detector.current_version = "1.9.10"
    return service, root, old


@pytest.mark.parametrize("old_version", ["1.5.0", "1.6.0", "1.7.0", "1.8.0"])
def test_migration_is_atomic_version_consistent_and_keeps_unknown_files(tmp_path, old_version):
    service, payload, old = fixture(tmp_path, old_version)
    target = tmp_path / "Actilo 中文"
    target.mkdir()
    personal = target / "user-notes.txt"
    personal.write_text("keep")
    plan = prepare(service, target, payload / "Windows-MCP.exe", registered=False, stop=lambda _: [])
    record = SetupJournal.open(service, plan).record
    assert not service.backend.executable.exists(), "A reconnecting client could reopen the old runtime"
    from windows_mcp.infrastructure.execution_security import require_runtime_available, InstallationInProgress
    with pytest.raises(InstallationInProgress):
        require_runtime_available(service.backend.home / ".windows-mcp")
    assert record["legacy"] == (["claude", "codex"] if old_version < "1.7.0" else [])
    shutil.copytree(payload, target, dirs_exist_ok=True)
    result = commit(service, plan)
    require_runtime_available(service.backend.home / ".windows-mcp")
    assert result["phase"] == "committed" and personal.read_text() == "keep"
    for key in ("codex", "claude"):
        state = service.detector.inspect(key)
        assert state.status == IntegrationStatus.INSTALLED_MANAGED and state.detected_version == "1.9.10"
    assert (plan.parent / "manifest.json").is_file()
    assert rollback(service, plan) == []  # Committed installs are never silently undone.
    assert old != service.backend.executable.read_bytes()


def test_preflight_conflict_does_not_delete_edited_skill(tmp_path):
    service, payload, _ = fixture(tmp_path, "1.5.0")
    path = service.backend.clients["codex"].skill_root / "windows-mcp/SKILL.md"
    path.write_text("user-edited")
    with pytest.raises(ManagerError, match="MIGRATION_CONFLICT"):
        prepare(service, tmp_path / "target", payload / "Windows-MCP.exe", registered=False, stop=lambda _: [])
    assert path.read_text() == "user-edited"


def test_failed_component_verification_restores_old_clients_and_program(tmp_path):
    from installer.health import HealthResult
    service, payload, old = fixture(tmp_path, "1.6.0")
    target = tmp_path / "target"
    configs = {key: profile.config.read_bytes() for key, profile in service.backend.clients.items() if profile.config.exists()}
    plan = prepare(service, target, payload / "Windows-MCP.exe", registered=False, stop=lambda _: [])
    shutil.copytree(payload, target, dirs_exist_ok=True)
    class Failed:
        async def probe(self, executable):
            return HealthResult(False, "PROBE_FAILED", "isolated failure", "test")
    service.health = Failed()
    with pytest.raises(ManagerError, match="INSTALL_ROLLED_BACK"):
        commit(service, plan)
    assert service.backend.executable.read_bytes() == old
    for key, expected in configs.items():
        assert service.backend.clients[key].config.read_bytes() == expected
    assert rollback(service, plan) == []


def test_tampered_setup_plan_is_rejected_before_restore(tmp_path):
    service, payload, _ = fixture(tmp_path, "1.5.0")
    plan = prepare(service, tmp_path / "target", payload / "Windows-MCP.exe", registered=False, stop=lambda _: [])
    row = json.loads(plan.read_text())
    row["payload"]["files"][0]["path"] = str(tmp_path / "unowned")
    plan.write_text(json.dumps(row))
    with pytest.raises(ManagerError, match="INVALID_SETUP_PLAN"):
        rollback(service, plan)


def test_owned_old_bundle_is_retired_before_copy_and_rollback_restores_it(tmp_path):
    service, payload, old = fixture(tmp_path, "1.7.0")
    old_runtime = service.backend.data / ("_runtime-" + "a" * 20)
    old_runtime.mkdir()
    dependency = old_runtime / "python312.dll"
    dependency.write_bytes(b"previous dependency")
    create(service.backend.data, old_runtime.name, "1.7.0")
    plan = prepare(service, tmp_path / "target", payload / "Windows-MCP.exe", registered=False, stop=lambda _: [])
    assert not dependency.exists() and not service.backend.executable.exists()
    assert rollback(service, plan) == []
    assert dependency.read_bytes() == b"previous dependency"
    assert service.backend.executable.read_bytes() == old


def test_reinstall_accepts_valid_stale_registration_without_missing_directory_failure(tmp_path, monkeypatch):
    import winreg
    from contextlib import nullcontext
    from installer.migration import UNINSTALL_KEY

    service, payload, _ = fixture(tmp_path, "1.7.0")
    missing = tmp_path / "removed-old-program"
    key = object()
    rows = [(name, value, winreg.REG_SZ) for name, value in {
        "DisplayName": "Windows-MCP 1.4.5", "DisplayVersion": "1.4.5", "Publisher": "CyanQX",
        "InstallLocation": str(missing)}.items()]
    open_key, query, enum = winreg.OpenKey, winreg.QueryInfoKey, winreg.EnumValue
    monkeypatch.setattr(winreg, "OpenKey", lambda hive, name, *a, **kw: nullcontext(key) if name == UNINSTALL_KEY else open_key(hive, name, *a, **kw))
    monkeypatch.setattr(winreg, "QueryInfoKey", lambda item: (0, len(rows), 0) if item is key else query(item))
    monkeypatch.setattr(winreg, "EnumValue", lambda item, index: rows[index] if item is key else enum(item, index))
    monkeypatch.setattr("installer.migration.restore_registration", lambda _: [])  # No real registry writes.
    plan = prepare(service, tmp_path / "target", payload / "Windows-MCP.exe", stop=lambda _: [])
    assert SetupJournal.open(service, plan).record["previous_install"]["root"] == str(missing)
    assert rollback(service, plan) == []
    assert not missing.exists()
