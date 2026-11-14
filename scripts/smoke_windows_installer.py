"""On an isolated CI runner, install to a chosen path, verify the payload and uninstall."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def integration_fixture(root, payload):
    """Install real managed clients in a fake home, then mark their receipts old."""
    from installer.settings import SettingsService
    from installer.operations import InstallerService
    from installer.health import HealthChecker
    from installer.models import Presence

    env = dict(os.environ)
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "DSH_HOME", "XDG_CONFIG_HOME", "OPENCODE_CONFIG", "PYINSTALLER_RESET_ENVIRONMENT"):
        env.pop(key, None)
    home = root / "client-home"
    home.mkdir()
    env.update(USERPROFILE=str(home), HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):],
               LOCALAPPDATA=str(home / "AppData/Local"), APPDATA=str(home / "AppData/Roaming"))
    settings = SettingsService(home, env=env)
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    class Discovery:
        def detect(self, profile):
            return Presence.DETECTED, "isolated installer acceptance"
    backend = settings.backend(ROOT / "skills/windows-mcp", payload.resolve())
    service = InstallerService(backend, manifest, discovery=Discovery(), health=HealthChecker(manifest, 90, home=root / "probe-home"))
    assert all(row["ok"] for row in service.install_targets(["codex", "claude"]))
    for key in ("codex", "claude"):
        path = backend.receipt_path(backend.clients[key])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["version"] = "0.0.0"
        path.write_text(json.dumps(receipt), encoding="utf-8")
    # Reproduce launches from an old PyInstaller parent whose _MEI is gone.
    env.update(_PYI_APPLICATION_HOME_DIR=str(root / "missing_MEI"), _PYI_PARENT_PROCESS_LEVEL="1",
               _PYI_ARCHIVE_FILE=str(root / "chosen-install-location/Windows-MCP.exe"))
    return service, env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=Path)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = hashlib.sha256(args.payload.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="windows-mcp-wizard-check-", ignore_cleanup_errors=True) as temporary:
        destination = Path(temporary) / "chosen-install-location"
        service, isolated_env = integration_fixture(Path(temporary), args.payload)
        first_log = Path(temporary) / "first-install.log"
        completed = subprocess.run([str(args.installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", "/TASKS=", f"/DIR={destination}", f"/LOG={first_log}"], env=isolated_env, timeout=240)
        states = {key: service.detector.inspect(key) for key in ("codex", "claude")}
        if completed.returncode or not all(state.detected_version == service.manifest["version"] for state in states.values()):
            print(json.dumps({"states": {key: state.to_dict() for key, state in states.items()}, "home": str(service.backend.home), "data": str(service.backend.data)}, ensure_ascii=True), flush=True)
            raw = first_log.read_bytes()
            print(json.dumps(raw.decode("utf-16" if raw.startswith(b"\xff\xfe") else "utf-8-sig", errors="replace")[-16000:], ensure_ascii=True), flush=True)
            raise AssertionError("Installed integrations were not synchronized by setup")
        executable = destination / "Windows-MCP.exe"
        assert hashlib.sha256(executable.read_bytes()).hexdigest() == expected
        uninstaller = destination / "unins000.exe"
        assert uninstaller.is_file()
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99}_is1"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as entry:
            assert str(destination) in winreg.QueryValueEx(entry, "UninstallString")[0]
        report = Path(temporary) / "installed-validation.json"
        subprocess.run([sys.executable, str(ROOT / "scripts/smoke_setup.py"), str(executable), "--output", str(report)], check=True, timeout=420, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        validation = json.loads(report.read_text(encoding="utf-8"))
        # Emulate the old manager's unqualified setup launch. The existing
        # registered installation must skip the guide and retain its path.
        executable.write_bytes(b"MZ isolated previous manager")
        update_log = Path(temporary) / "update-install.log"
        bootstrap = subprocess.Popen([str(args.installer.resolve()), "/WMCPNOLAUNCH=1", f"/LOG={update_log}"], env=isolated_env)
        try:
            bootstrap.wait(timeout=60)
        except subprocess.TimeoutExpired:
            if update_log.exists():
                raw = update_log.read_bytes()
                print(raw.decode("utf-16" if raw.startswith(b"\xff\xfe") else "utf-8-sig", errors="replace")[-8000:], flush=True)
            raise
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                raw = update_log.read_bytes() if update_log.exists() else b""
                content = raw.decode("utf-16" if raw.startswith(b"\xff\xfe") else "utf-8-sig", errors="replace")
                if executable.is_file() and hashlib.sha256(executable.read_bytes()).hexdigest() == expected and "WMCP_UPDATE_MODE=progress-only" in content and "Installation process succeeded" in content:
                    break
            except OSError:
                pass  # Installer may hold the payload briefly while replacing it.
            time.sleep(.25)
        else:
            raise AssertionError("Existing installation did not complete its progress-only update")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as entry:
            assert str(destination) in winreg.QueryValueEx(entry, "UninstallString")[0]
        # Use the exact flags passed by UpdateService.launch_installer. Both
        # silent modes must complete without competing wizard Next messages.
        silent_updates = []
        for mode in ("/SILENT", "/VERYSILENT"):
            executable.write_bytes(b"MZ isolated previous manager")
            silent_log = Path(temporary) / (mode[1:].lower() + "-update.log")
            subprocess.run([str(args.installer.resolve()), mode, "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART", "/WMCPUPDATE=1", "/WMCPNOLAUNCH=1", f"/DIR={destination}", f"/LOG={silent_log}"], env=isolated_env, check=True, timeout=180)
            assert hashlib.sha256(executable.read_bytes()).hexdigest() == expected
            raw = silent_log.read_bytes()
            content = raw.decode("utf-16" if raw.startswith(b"\xff\xfe") else "utf-8-sig", errors="replace")
            assert "WMCP_UPDATE_MODE=progress-only" in content
            assert "Installation process succeeded" in content
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as entry:
                assert str(destination) in winreg.QueryValueEx(entry, "UninstallString")[0]
            silent_updates.append(mode)
        subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=120)
        deadline = time.monotonic() + 30
        while (executable.exists() or uninstaller.exists()) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not executable.exists(), "Installed manager was not removed"
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("System uninstall entry was not removed")
    result = {"installer": args.installer.name, "sha256": hashlib.sha256(args.installer.read_bytes()).hexdigest(), "chosen_directory_installation": "PASS", "automatic_component_sync": "PASS", "no_manual_component_step": True, "stale_pyinstaller_parent_environment": "PASS", "system_uninstall_registration": "PASS", "existing_install_progress_only_update": "PASS", "silent_update_modes": silent_updates, "silent_updates": "PASS", "original_install_location_preserved": "PASS", "uninstall": "PASS", "payload_sha256": expected, "actual_executable_validation": validation}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
