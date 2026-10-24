import json

import pytest

from installer.backend import Configuration
from installer.health import HealthResult
from installer.models import ManagerError
from installer.storage import Journal, ProcessLock
from tests.installer_helpers import make_service


class FailedHealth:
    async def probe(self, _):
        return HealthResult(False, "MCP_TIMEOUT", "timeout", "2026-10-15", 1)


def test_health_failure_rolls_back_files_receipt_and_runtime(tmp_path):
    service = make_service(tmp_path, health=FailedHealth())
    profile = service.backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    original = b"# exact original\nmodel='keep'\n"
    profile.config.write_bytes(original)
    result = service.install_targets(["codex"])[0]
    assert result["error_code"] == "MCP_TIMEOUT"
    assert profile.config.read_bytes() == original
    assert not service.backend.receipt_path(profile).exists()
    assert not service.backend.executable.exists()
    assert service.backups()[0]["state"] == "rolled_back"


def test_real_subprocess_cannot_mutate_while_manager_holds_lock(tmp_path):
    import subprocess
    import sys
    service = make_service(tmp_path)
    lockfile = service.backend.home / ".windows-mcp" / "maintenance.lock"
    script = "from pathlib import Path; from installer.storage import ProcessLock; import sys; lock=ProcessLock(Path(sys.argv[1])); lock.__enter__()"
    with service.lock():
        result = subprocess.run([sys.executable, "-c", script, str(lockfile)], capture_output=True, timeout=20)
        assert result.returncode != 0 and b"INSTALLER_BUSY" in result.stderr
        assert not service.backend.executable.exists()


def test_delayed_cleanup_rechecks_hash_and_references(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    pending = service.backend.data / "cleanup-pending.json"
    pending.write_text(json.dumps({"path": str(service.backend.executable), "hash": __import__("hashlib").sha256(service.backend.executable.read_bytes()).hexdigest()}))
    assert service.process_pending_cleanup() is False
    assert service.backend.executable.is_file()
    pending.write_text(json.dumps({"path": str(service.backend.executable), "hash": "tampered"}))
    with pytest.raises(ManagerError, match="CLEANUP_CHANGED"):
        service.process_pending_cleanup()
    assert service.backend.executable.is_file()


def test_journal_recovery_never_overwrites_changes_it_did_not_write(tmp_path):
    target = tmp_path / "file.txt"
    target.write_bytes(b"before")
    journal = Journal(tmp_path / "data", "codex", "repair", [target])
    journal.wrote(target, "planned-hash")
    target.write_bytes(b"user changed it")
    assert journal.rollback() == ["file.txt"]
    assert target.read_bytes() == b"user changed it"


def test_cross_process_lock_blocks_another_writer(tmp_path):
    lock = tmp_path / "lock"
    with ProcessLock(lock):
        with pytest.raises(ManagerError, match="INSTALLER_BUSY"):
            with ProcessLock(lock):
                pytest.fail("second writer acquired lock")


def test_restore_changes_only_windows_mcp_and_preserves_later_other_settings(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["claude"])
    snapshot = service.backup(["claude"])[0]
    profile = service.backend.clients["claude"]
    value = json.loads(profile.config.read_text())
    value["newPersonalSetting"] = "keep"
    value["mcpServers"]["windows-mcp"]["args"] = ["--changed"]
    profile.config.write_text(json.dumps(value), encoding="utf-8")
    plan = service.preview_restore(snapshot)
    service.restore(snapshot, plan["digest"])
    actual = json.loads(profile.config.read_text())
    assert actual["newPersonalSetting"] == "keep"
    assert actual["mcpServers"]["windows-mcp"]["args"] == ["--serve"]


@pytest.mark.parametrize("text", [
    '{\n// leading comment\n"model": "keep", "mcp": {"other": {"command": ["keep"]}}\n}',
    "{/* top */ mcp: {other: {command: ['keep']},}, editor: 'keep',}",
    '{"mcp":{"other":{"command":["keep"]},"windows-mcp":{"command":["old"]}}}',
])
def test_jsonc_patch_preserves_unrelated_comments_and_values_through_install_uninstall(tmp_path, text):
    service = make_service(tmp_path)
    from installer.backend import profiles
    profile = next(profile for profile in profiles(service.backend.home, {}, include_legacy=True) if profile.id == "opencode")
    config = Configuration(profile, text.encode())
    before = config.document
    config.set({"type": "local", "command": ["new"]})
    encoded = config.encode()
    if "leading comment" in text:
        assert "// leading comment" in encoded.decode()
    if "/* top */" in text:
        assert "/* top */" in encoded.decode()
    updated = Configuration(profile, encoded)
    assert updated.document["mcp"]["other"] == before["mcp"]["other"]
    updated.set(None)
    assert Configuration(profile, updated.encode()).get() is None
