"""Portable boundary, relocation, archive integrity and real distribution update selection."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from installer.distribution import portable_name, setup_name, verify_portable
from installer.models import ManagerError
from installer.settings import SettingsService
from installer.updates import UpdateService
from scripts.build_portable import package
from tests.test_health_update import Response, Session, release
from windows_mcp.infrastructure.runtime_paths import PORTABLE_MARKER, PORTABLE_MODE, portable_data_directory


def bundle(tmp_path):
    root = tmp_path / "portable"
    root.mkdir()
    (root / PORTABLE_MARKER).write_text(json.dumps(PORTABLE_MODE))
    return root


def test_manager_and_copied_service_find_the_same_data_not_working_directory(tmp_path):
    root = bundle(tmp_path)
    assert portable_data_directory(root / "Windows-MCP.exe", frozen=True) == root / "data"
    assert portable_data_directory(root / "data/Windows-MCP.exe", frozen=True) == root / "data"
    assert portable_data_directory(tmp_path / "other/Windows-MCP.exe", frozen=True) is None
    assert portable_data_directory(root / "Windows-MCP.exe", frozen=False) is None


def test_portable_marker_cannot_redirect_to_an_arbitrary_directory(tmp_path):
    root = bundle(tmp_path)
    (root / PORTABLE_MARKER).write_text(json.dumps({**PORTABLE_MODE, "data": "../outside"}))
    with pytest.raises(ValueError, match="portable marker"):
        portable_data_directory(root / "Windows-MCP.exe", frozen=True)


def test_portable_settings_do_not_import_user_preferences_or_move_client_paths(tmp_path):
    home = tmp_path
    installed = SettingsService(home, env={})
    installed.save({**installed.value, "theme": "dark"})
    before = installed.path.read_bytes()
    data = bundle(tmp_path) / "data"
    portable = SettingsService(home, env={}, portable_data=data)
    assert portable.value["theme"] == "light"
    portable.save({**portable.value, "health_timeout": 15})
    assert portable.path == data / "config/manager.json"
    assert portable.path_details() == installed.path_details()
    assert installed.path.read_bytes() == before
    assert SettingsService(home, env={}, portable_data=data).value["health_timeout"] == 15
    with pytest.raises(ManagerError, match="PORTABLE_DATA_FIXED"):
        portable.save({**portable.value, "data_dir": str(home / "elsewhere")})


def test_moved_portable_directory_with_managed_receipt_stops_before_writing(tmp_path):
    home = tmp_path
    original = bundle(tmp_path)
    settings = SettingsService(home, env={}, portable_data=original / "data")
    settings.save(settings.value)
    receipts = original / "data/receipts"
    receipts.mkdir()
    (receipts / "codex.json").write_text("{}")
    moved = tmp_path / "moved"
    original.rename(moved)
    before = (moved / "data/config/manager.json").read_bytes()
    with pytest.raises(ManagerError, match="PORTABLE_DIRECTORY_MOVED"):
        SettingsService(home, env={}, portable_data=moved / "data")
    assert (moved / "data/config/manager.json").read_bytes() == before


def test_private_portable_backups_and_settings_are_protected_but_workspace_is_usable(tmp_path, monkeypatch):
    from windows_mcp.infrastructure import policy
    data = tmp_path / "portable/data"
    monkeypatch.setattr(policy, "CONFIG_DIR", data / "config")
    monkeypatch.setattr(policy, "portable_data_directory", lambda: data)
    gate = policy.PolicyGate(roots=[data])
    for path in (data / "config/manager.json", data / "transactions/backup", data / "Windows-MCP.exe"):
        with pytest.raises(policy.PolicyBlocked):
            gate.check_path(path)
    assert gate.check_path(data / "config/workspace/example.txt") == data / "config/workspace/example.txt"


def test_portable_archive_rejects_extra_entries_including_path_traversal(tmp_path):
    executable = tmp_path / "manager.exe"
    executable.write_bytes(b"MZ" + b"x" * 2048)
    result = package(executable, tmp_path / "out", "1.3.0")
    archive = tmp_path / "out" / portable_name("1.3.0")
    assert result["payload_sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive, "a") as content:
        content.writestr("../unexpected.txt", b"outside")
    with pytest.raises(ValueError, match="unexpected entries"):
        verify_portable(archive, "1.3.0")
    assert not (tmp_path / "unexpected.txt").exists()


def test_portable_update_verifies_whole_zip_and_stages_only_its_manager(tmp_path):
    executable = tmp_path / "manager.exe"
    executable.write_bytes(b"MZ" + b"x" * 2048)
    package(executable, tmp_path / "out", "1.3.0")
    content = (tmp_path / "out" / portable_name("1.3.0")).read_bytes()
    metadata = release(content)
    metadata["assets"][0].update(name=portable_name("1.3.0"), browser_download_url="https://github.com/CyanQX/Windows11-mcp/releases/download/v1.3.0/" + portable_name("1.3.0"))
    service = UpdateService(tmp_path / "data", "1.2.2", Session(Response(metadata), Response(content=content)), portable=True)
    plan = service.check()
    assert plan["kind"] == "portable"
    staged = service.download(plan)
    assert staged.read_bytes() == executable.read_bytes()
    assert plan["archive_sha256"] == hashlib.sha256(content).hexdigest()
    assert plan["sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()


def test_installation_wizard_is_never_used_as_manager_replacement(tmp_path, monkeypatch):
    payload = b"MZ" + b"x" * 2048
    metadata = release(payload)
    metadata["assets"][0].update(name=setup_name("1.3.0"), browser_download_url="https://github.com/CyanQX/Windows11-mcp/releases/download/v1.3.0/" + setup_name("1.3.0"))
    service = UpdateService(tmp_path / "data", "1.2.2", Session(Response(metadata), Response(content=payload)))
    plan = service.check()
    staged = service.download(plan)
    assert plan["kind"] == "installer"
    with pytest.raises(ManagerError, match="UPDATE_INSTALLER_REQUIRED"):
        service.prepare(staged, plan, Path("current.exe"))
    calls = []
    monkeypatch.setattr("installer.updates.subprocess.Popen", lambda args, **kwargs: calls.append(args))
    service.launch_installer(staged, plan)
    assert calls == [[str(staged)]]
    staged.write_bytes(b"MZ tampered")
    with pytest.raises(ManagerError, match="UPDATE_VERIFY_FAILED"):
        service.launch_installer(staged, plan)
    assert len(calls) == 1
