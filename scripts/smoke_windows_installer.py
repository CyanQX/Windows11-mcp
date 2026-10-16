"""On an isolated CI runner, install to a chosen path, verify the payload and uninstall."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=Path)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = hashlib.sha256(args.payload.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="windows-mcp-wizard-check-", ignore_cleanup_errors=True) as temporary:
        destination = Path(temporary) / "chosen-install-location"
        subprocess.run([str(args.installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", "/TASKS=", f"/DIR={destination}"], check=True, timeout=180)
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
        bootstrap = subprocess.Popen([str(args.installer.resolve()), "/WMCPNOLAUNCH=1", f"/LOG={update_log}"])
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
    result = {"installer": args.installer.name, "sha256": hashlib.sha256(args.installer.read_bytes()).hexdigest(), "chosen_directory_installation": "PASS", "system_uninstall_registration": "PASS", "existing_install_progress_only_update": "PASS", "original_install_location_preserved": "PASS", "uninstall": "PASS", "payload_sha256": expected, "actual_executable_validation": validation}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
