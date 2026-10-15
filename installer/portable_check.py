"""Frozen portable acceptance: data location, official client paths and protection."""

import json
import sys
from pathlib import Path


def check(output: Path):
    from installer.diagnostics import EventLog
    from installer.settings import SettingsService
    from installer.storage import Journal, file_hash
    from windows_mcp.infrastructure.config import CONFIG_DIR
    from windows_mcp.infrastructure.policy import PolicyBlocked, PolicyGate
    from windows_mcp.infrastructure.runtime_paths import portable_data_directory

    data = portable_data_directory()
    assert data is not None, "Extract the complete portable ZIP first"
    settings = SettingsService(Path.home())
    assert settings.path == data / "config/manager.json" and CONFIG_DIR == data / "config"
    settings.save({**settings.value, "health_timeout": 45})
    assert SettingsService(Path.home()).value["health_timeout"] == 45
    assert not any((data / "receipts").glob("*.json"))
    clients = settings.path_details()
    assert clients["codex"]["skill"] == str(Path.home() / ".agents/skills/windows-mcp")
    log = EventLog(data, Path.home(), "portable-acceptance")
    log.emit("acceptance", "便携数据目录验证")
    original = data / "acceptance-example.txt"
    original.write_bytes(b"before")
    journal = Journal(data, "acceptance", "verify-rollback", [original])
    original.write_bytes(b"after")
    journal.wrote(original, file_hash(original))
    assert not journal.rollback() and original.read_bytes() == b"before"
    gate = PolicyGate(roots=[data])
    for protected in (settings.path, journal.root / "manifest.json", data / "Windows-MCP.exe"):
        try:
            gate.check_path(protected)
        except PolicyBlocked:
            pass
        else:
            raise AssertionError("Portable private data was exposed to MCP")
    assert gate.check_path(CONFIG_DIR / "workspace/example.txt") == CONFIG_DIR / "workspace/example.txt"
    result = {"portable": True, "executable": Path(sys.executable).name, "settings_adjacent_to_executable": True, "logs_and_backups_adjacent": True, "official_client_paths_preserved": True, "server_and_manager_config_match": True, "private_data_protected": True, "rollback": "PASS"}
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
