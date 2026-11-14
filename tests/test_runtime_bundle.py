"""Installation reconciliation and compatibility migration use isolated files."""
import copy
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from installer.bundle import BUNDLE_FILE, create, verify, stage_support, validate
from installer.models import ManagerError
from installer.storage import file_hash
from tests.installer_helpers import make_service


def payload(root, value=b"MZ new executable", contents="_runtime-" + "a" * 20):
    root.mkdir(parents=True, exist_ok=True)
    (root / "Windows-MCP.exe").write_bytes(value)
    support = root / contents
    support.mkdir(exist_ok=True)
    (support / "python312.dll").write_bytes(b"isolated runtime")
    return create(root, contents, "1.8.0")


def test_bundle_is_fully_verified_and_copy_conflicts_do_not_overwrite(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    record = payload(source)
    stage_support(source, target, record)
    verify(target, record, executable=False)
    dll = target / record["contents"] / "python312.dll"
    dll.write_bytes(b"user change")
    with pytest.raises(ValueError, match="verification"):
        stage_support(source, target, record)
    assert dll.read_bytes() == b"user change"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "Windows-MCP.exe/child", "_runtime-" + "a" * 20 + "/../evil", "C:/evil", "_runtime-" + "a" * 20 + "\\evil"])
def test_bundle_rejects_ambiguous_paths(tmp_path, name):
    record = payload(tmp_path)
    record["files"][name] = next(iter(record["files"].values()))
    with pytest.raises(ValueError):
        validate(record)


def test_new_manager_automatically_reconciles_all_owned_clients_and_is_idempotent(tmp_path):
    service = make_service(tmp_path)
    assert all(row["ok"] for row in service.install_targets(["codex", "claude"]))
    service.manifest["version"] = service.backend.version = "1.8.0"
    service.detector.current_version = "1.8.0"
    service.backend.source_executable.write_bytes(b"MZ next release")
    results = service.synchronize_installed()
    assert len(results) == 2 and all(row["ok"] for row in results)
    assert service.backend.executable.read_bytes() == b"MZ next release"
    assert all(service.detector.inspect(key).detected_version == "1.8.0" for key in ("codex", "claude"))
    before = len(service.backups())
    assert service.synchronize_installed() == []
    assert len(service.backups()) == before


def test_automatic_sync_preserves_modified_skill_and_other_clients(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex", "claude"])
    service.backend.source_executable.write_bytes(b"MZ next release")
    changed = service.backend.clients["codex"].skill_root / "windows-mcp/SKILL.md"
    changed.write_text("personal content")
    before = service.backend.executable.read_bytes()
    with pytest.raises(ManagerError, match="AUTO_SYNC_CONFLICT"):
        service.synchronize_installed()
    assert changed.read_text() == "personal content" and service.backend.executable.read_bytes() == before


def test_upgrade_removes_only_obsolete_owned_files_and_preserves_personal_files(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    folder = service.backend.clients["codex"].skill_root / "windows-mcp"
    (folder / "personal.txt").write_text("personal")
    (service.backend.skill / "reference.md").unlink()
    service.backend.source_executable.write_bytes(b"MZ changed core")
    assert all(row["ok"] for row in service.synchronize_installed())
    assert not (folder / "reference.md").exists() and (folder / "personal.txt").read_text() == "personal"


def test_upgrade_new_filename_collision_never_overwrites_unmanaged_personal_content(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    folder = service.backend.clients["codex"].skill_root / "windows-mcp"
    (folder / "new-guide.md").write_text("user guide")
    (service.backend.skill / "new-guide.md").write_text("product guide")
    before = service.backend.executable.read_bytes()
    service.backend.source_executable.write_bytes(b"MZ changed core")
    with pytest.raises(ManagerError):
        service.synchronize_installed()
    assert (folder / "new-guide.md").read_text() == "user guide" and service.backend.executable.read_bytes() == before


def test_bundle_manifest_is_journaled_and_runtime_tampering_blocks_probe(tmp_path):
    service = make_service(tmp_path)
    record = payload(tmp_path)
    service.backend.source_executable = tmp_path / "Windows-MCP.exe"
    assert service.install_targets(["codex"])[0]["ok"]
    receipt = json.loads(service.backend.receipt_path(service.backend.clients["codex"]).read_text())
    assert receipt["bundle_sha256"] == file_hash(service.backend.data / BUNDLE_FILE)
    dll = service.backend.data / record["contents"] / "python312.dll"
    dll.write_bytes(b"corrupt")
    assert not service.detector.inspect("codex").install_eligible
    with pytest.raises(ManagerError, match="UNTRUSTED_RUNTIME"):
        service.verify("codex")


def test_small_installer_import_does_not_import_server_or_format_libraries():
    code = "import installer.app, installer.settings; import sys; assert not set(('fastmcp','posthog','tomlkit','json5','ruamel.yaml','PySide6')) & set(sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)


def test_independent_children_reset_pyinstaller_environment(monkeypatch):
    from windows_mcp.infrastructure.process_env import independent_environment
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "deleted/_MEI")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    env = independent_environment({"EXAMPLE": "kept"})
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["EXAMPLE"] == "kept"
    assert not any(name.startswith("_PYI_") for name in env)


def test_legacy_update_bridge_preserves_plan_and_resets_child_environment(tmp_path, monkeypatch):
    from installer.legacy_migration import migrate
    monkeypatch.setattr("installer.legacy_migration.installed_version", lambda _: "1.7.0")
    source = tmp_path / "payload"
    record = payload(source)
    data = tmp_path / "data"
    stage = data / "updates" / ("a" * 32)
    stage.mkdir(parents=True)
    executable = stage / "next.exe"
    executable.write_bytes(b"MZ legacy bridge")
    target = tmp_path / "installed/Windows-MCP.exe"
    target.parent.mkdir()
    target.write_bytes(b"MZ old")
    nonce = "b" * 32
    row = {"nonce": nonce, "data": str(data), "created": time.time(), "staged": str(executable), "target": str(target), "backup": str(stage / "previous.exe"), "sha256": file_hash(executable), "old_sha256": file_hash(target), "parents": [{"pid": 42, "created": 1}]}
    original = copy.deepcopy(row)
    plan = stage / "plan.json"
    plan.write_text(json.dumps(row))
    calls = []
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "deleted")
    assert migrate(executable, source, [f"--apply-update={plan}", f"--update-nonce={nonce}"], lambda *args, **kw: calls.append((args, kw))) == 0
    updated = json.loads(plan.read_text())
    assert updated["parents"] == original["parents"] and updated["old_sha256"] == original["old_sha256"]
    assert updated["sha256"] == file_hash(executable) == record["files"]["Windows-MCP.exe"]["sha256"]
    assert updated["bundle_sha256"] == file_hash(stage / BUNDLE_FILE)
    assert calls[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "_PYI_APPLICATION_HOME_DIR" not in calls[0][1]["env"]
    assert target.read_bytes() == b"MZ old"


def test_legacy_version_reader_uses_bounded_embedded_manifest(tmp_path):
    import struct
    import zlib
    from installer.legacy_migration import installed_version
    raw = json.dumps({"version": "1.5.0"}).encode()
    data = zlib.compress(raw)
    name = b"manifest.json\0"
    toc = struct.pack("!IIIIBc", 18 + len(name), 0, len(data), len(raw), 1, b"x") + name
    cookie = struct.pack("!8sIIII64s", b"MEI\x0c\x0b\x0a\x0b\x0e", len(data) + len(toc) + 88, len(data), len(toc), 312, b"python312.dll")
    executable = tmp_path / "legacy.exe"
    executable.write_bytes(b"MZfixture" + data + toc + cookie)
    assert installed_version(executable) == "1.5.0"
    executable.write_bytes(b"MZ invalid")
    assert installed_version(executable) is None


def test_legacy_first_launch_materializes_once_and_preserves_adjacent_data(tmp_path):
    from installer.legacy_migration import migrate
    source = tmp_path / "payload"
    payload(source)
    target = tmp_path / "portable"
    target.mkdir()
    executable = target / "Windows-MCP.exe"
    executable.write_bytes(b"MZ bridge")
    (target / "WindowsMCP.portable.json").write_text('{"schema":1,"mode":"portable"}')
    data = target / "data"
    data.mkdir()
    (data / "keep.txt").write_text("keep")
    calls = []
    migrate(executable, source, ["--portable-check=isolated.json"], lambda *args, **kw: (calls.append(args), SimpleNamespace(wait=lambda: 0))[1])
    verify(target)
    assert (data / "keep.txt").read_text() == "keep"
    assert len(calls) == 1 and executable.read_bytes() == b"MZ new executable"


def test_automatic_sync_rolls_back_every_client_after_failed_connection(tmp_path):
    from tests.installer_helpers import Healthy
    from installer.health import HealthResult
    service = make_service(tmp_path)
    service.install_targets(["codex", "claude"])
    paths = set(service.paths("codex") + service.paths("claude"))
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    service.backend.source_executable.write_bytes(b"MZ bad next release")
    class Unhealthy(Healthy):
        async def probe(self, executable):
            return HealthResult(False, "BAD_VERSION", "isolated failure", "2026-11-14T00:00:00Z", 1)
    service.health = Unhealthy()
    with pytest.raises(ManagerError, match="UPGRADE_ROLLED_BACK"):
        service.synchronize_installed()
    assert all((path.read_bytes() if path.exists() else None) == content for path, content in before.items())


def test_bundle_snapshot_reuses_verification_but_detects_changes(tmp_path, monkeypatch):
    from installer.storage import hash_snapshot
    import installer.storage as storage
    record = payload(tmp_path)
    calls = []
    original = storage.file_hash
    def observed(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(storage, "file_hash", observed)
    with hash_snapshot():
        verify(tmp_path, record)
        first = len(calls)
        verify(tmp_path, record)
        assert len(calls) == first
        (tmp_path / record["contents"] / "python312.dll").write_bytes(b"changed")
        with pytest.raises(ValueError):
            verify(tmp_path, record)
