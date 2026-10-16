"""Install cancellation and durable undo restore the complete pre-install baseline."""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from installer.backend import Configuration, mcp_entry
from installer.models import ManagerError
from installer.operations import InstallerService
from tests.installer_helpers import Healthy, make_service


def snapshot(service, keys):
    return {path: path.read_bytes() if path.exists() else None for key in keys for path in service.paths(key)}


def assert_baseline(before):
    assert all((path.read_bytes() if path.exists() else None) == content for path, content in before.items())


def test_completed_multi_target_install_undo_survives_manager_restart(tmp_path):
    service = make_service(tmp_path)
    profile = service.backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    profile.config.write_bytes(b"# exact bytes\nmodel = 'keep'\n")
    keys = ["codex", "claude", "deepseek"]
    before = snapshot(service, keys)
    assert all(row["ok"] for row in service.install_targets(keys))
    restarted = InstallerService(service.backend, service.manifest, discovery=service.detector.discovery, health=Healthy())
    transaction = restarted.latest_install()
    plan = restarted.preview_undo_install(transaction)
    assert not plan["conflicts"]
    assert all(row["ok"] for row in restarted.undo_install(transaction, plan["digest"]))
    assert_baseline(before)
    assert restarted.latest_install() is None


def test_cancel_during_second_probe_closes_probe_and_undoes_first_target_too(tmp_path):
    cancel, entered, closed = threading.Event(), threading.Event(), threading.Event()
    class Probe(Healthy):
        count = 0
        async def probe(self, executable):
            self.count += 1
            if self.count == 2:
                entered.set()
                try:
                    await asyncio.sleep(120)
                finally:
                    closed.set()
            return await super().probe(executable)
    service = make_service(tmp_path, health=Probe())
    keys = ["codex", "claude", "deepseek"]
    before = snapshot(service, keys)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(service.install_targets, keys, cancel=cancel)
        assert entered.wait(5)
        started = time.monotonic()
        cancel.set()
        results = future.result(timeout=5)
    assert time.monotonic() - started < 3
    assert closed.is_set() and all(row["cancelled"] for row in results)
    assert_baseline(before)


def test_undo_rejects_user_edits_and_stale_preview_before_any_writes(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    transaction = service.latest_install()
    plan = service.preview_undo_install(transaction)
    profile = service.backend.clients["codex"]
    profile.config.write_bytes(profile.config.read_bytes() + b"\n# new user preference\n")
    changed = snapshot(service, ["codex"])
    with pytest.raises(ManagerError, match="PLAN_CHANGED"):
        service.undo_install(transaction, plan["digest"])
    new = service.preview_undo_install(transaction)
    assert new["conflicts"]
    with pytest.raises(ManagerError, match="UNDO_CONFLICT"):
        service.undo_install(transaction, new["digest"])
    assert_baseline(changed)


def test_undo_preserves_runtime_used_by_another_install(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    first = service.latest_install()
    service.install_targets(["claude"])
    second = service.latest_install()
    plan = service.preview_undo_install(first)
    assert plan["conflicts"]  # The first install created the now-shared runtime.
    plan = service.preview_undo_install(second)
    assert not plan["conflicts"]
    assert service.undo_install(second, plan["digest"])[0]["ok"]
    assert service.backend.executable.exists()
    assert service.detector.inspect("codex").receipt_valid


def test_cancel_preserves_runtime_if_unrelated_connection_appears(tmp_path):
    cancel = threading.Event()
    service = make_service(tmp_path)
    class ExternalReference(Healthy):
        async def probe(self, executable):
            profile = service.backend.clients["claude"]
            profile.config.parent.mkdir(parents=True, exist_ok=True)
            config = Configuration(profile, None)
            config.set(mcp_entry(profile, executable))
            profile.config.write_bytes(config.encode())
            cancel.set()
            return await super().probe(executable)
    service.health = ExternalReference()
    result = service.install_targets(["codex"], cancel=cancel)[0]
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert service.backend.executable.exists()
    assert not service.backend.clients["codex"].config.exists()


def test_cancel_during_file_writes_restores_exact_config_bytes(tmp_path, monkeypatch):
    cancel = threading.Event()
    service = make_service(tmp_path)
    profile = service.backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    profile.config.write_bytes(b"# keep this exact spacing\nmodel =  'keep'\n")
    before = snapshot(service, ["codex", "claude"])
    emit = service.log.emit
    def intercept(phase, message, **kwargs):
        value = emit(phase, message, **kwargs)
        if phase == "skill":
            cancel.set()
        return value
    monkeypatch.setattr(service.log, "emit", intercept)
    assert all(row["cancelled"] for row in service.install_targets(["codex", "claude"], cancel=cancel))
    assert_baseline(before)


def test_undo_rejects_corrupt_snapshot_without_touching_install(tmp_path):
    import json
    service = make_service(tmp_path)
    profile = service.backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    profile.config.write_bytes(b"model='keep'\n")
    service.install_targets(["codex"])
    transaction = service.latest_install()
    root = service.backend.data / "transactions" / transaction
    record = json.loads((root / "manifest.json").read_text())
    row = next(item for item in record["files"] if item["path"] == str(profile.config))
    (root / row["slot"]).write_bytes(b"corrupt")
    before = snapshot(service, ["codex"])
    with pytest.raises(ManagerError, match="BACKUP_CORRUPT"):
        service.preview_undo_install(transaction)
    assert_baseline(before)
