"""CI-only actual released installers -> Actilo, with owned receipt fixtures.

The runner must be disposable: every installer intentionally uses the real
project AppId. Never run this against a developer's installed manager.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args, env, *, timeout=300):
    result = subprocess.run([str(item) for item in args], env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Native installer acceptance failed (exit " + str(result.returncode) + "): " + Path(str(args[0])).name)


def previous_release(root, version):
    api = "https://api.github.com/repos/CyanQX/Windows11-mcp"
    session = requests.Session()
    if os.environ.get("GITHUB_TOKEN"):
        session.headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    response = session.get(api + "/releases/tags/v" + version, timeout=(10, 60))
    response.raise_for_status()
    row = response.json()
    name = f"Windows-MCP-Setup-v{version}-x64.exe"
    asset = next(item for item in row["assets"] if item["name"] == name)
    expected = asset.get("digest", "").removeprefix("sha256:")
    if len(expected) != 64:
        checksum = next(item for item in row["assets"] if item["name"] == "SHA256SUMS.txt")
        manifest = requests.get(checksum["browser_download_url"], timeout=(10, 60))
        manifest.raise_for_status()
        expected = next(line.split()[0] for line in manifest.text.splitlines() if line.endswith("  " + name))
    path = root / name
    digest, total = hashlib.sha256(), 0
    with requests.get(asset["browser_download_url"], stream=True, timeout=(10, 90)) as download:
        download.raise_for_status()
        with path.open("wb") as stream:
            for block in download.iter_content(1048576):
                total += len(block)
                if total > min(asset["size"], 600 * 1048576):
                    raise ValueError("Legacy download exceeded declared size")
                stream.write(block)
                digest.update(block)
    assert total == asset["size"] and digest.hexdigest() == expected
    source = session.get(api + "/zipball/v" + version, timeout=(10, 90))
    source.raise_for_status()
    if len(source.content) > 64 * 1048576:
        raise ValueError("Legacy source archive exceeds size limit")
    skill = root / "legacy-skill"
    skill.mkdir()
    with zipfile.ZipFile(io.BytesIO(source.content)) as archive:
        prefix = archive.namelist()[0].split("/", 1)[0] + "/"
        manifest = json.loads(archive.read(prefix + "manifest.json"))
        assert manifest["version"] == version
        for item in archive.infolist():
            if not item.filename.startswith(prefix + "skills/windows-mcp/") or item.is_dir():
                continue
            relative = PurePosixPath(item.filename.removeprefix(prefix + "skills/windows-mcp/"))
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative) or ":" in str(relative) or item.file_size > 4 * 1048576:
                raise ValueError("Unsafe legacy Skill archive")
            target = skill.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
    assert (skill / "SKILL.md").is_file()
    return path, skill, manifest, expected


def check(installer, payload, root, version):
    from scripts.smoke_actilo import environment
    from installer.settings import SettingsService
    from installer.operations import InstallerService
    from installer.migration import SetupJournal

    root.mkdir()
    old_setup, skill, old_manifest, fingerprint = previous_release(root, version)
    home = root / "home"
    home.mkdir()
    env = environment(home)
    old_dir, new_dir = root / "旧版本 安装", root / "Actilo 新版"
    run([old_setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", "/TASKS=", "/WMCPNOLAUNCH=1", "/DIR=" + str(old_dir)], env)
    old_exe = old_dir / "Windows-MCP.exe"
    assert old_exe.is_file()
    personal = old_dir / "personal-notes.txt"
    personal.write_text("must survive project cleanup", encoding="utf-8")
    settings = SettingsService(home, env=env)
    backend = settings.backend(skill, old_exe)
    InstallerService(backend, old_manifest)
    # Exact schema-2 ownership fixtures, built from the actual released old
    # executable and Skill, without requiring a broken old server to be healthy.
    for key in ("codex", "claude"):
        backend.install(backend.clients[key])
    before = {key: backend.clients[key].config.read_bytes() for key in ("codex", "claude")}
    old_server = subprocess.Popen([str(backend.executable), "--serve"], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        time.sleep(2)
        run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", "/TASKS=", "/WMCPNOLAUNCH=1", "/ACTILOMANUALMIGRATION=1", "/DIR=" + str(new_dir)], env, timeout=420)
        old_server.wait(timeout=15)
        assert personal.read_text(encoding="utf-8") == "must survive project cleanup"
        if version < "1.7.0":
            assert not old_exe.exists(), "Owned legacy manager survived cleanup"
        assert not (old_dir / "unins000.exe").exists(), "Obsolete uninstaller survived cleanup"
        current = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        fresh = InstallerService(settings.backend(ROOT / "skills/windows-mcp", payload), current)
        states = {key: fresh.detector.inspect(key) for key in ("codex", "claude")}
        assert all(state.detected_version == "1.9.10" and state.receipt_valid for state in states.values())
        plans = list((backend.data / "setup/transactions").glob("*/manifest.json"))
        committed = [SetupJournal.open(fresh, path).record for path in plans]
        assert any(row["state"] == "committed" and row["phase"] == "verified" for row in committed)
        assert all(fresh.backend.clients[key].config.read_bytes() == before[key] for key in before)  # Stable MCP command paths.
        run([new_dir / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], env)
        return {"from_version": version, "to_version": "1.9.10", "actual_legacy_setup_sha256": fingerprint,
                "actual_legacy_payload": True, "receipt_fixture": "schema 2, actual legacy Skill and executable",
                "managed_profiles": ["codex", "claude"], "stopped_owned_server": True, "owned_backup": True,
                "user_file_preserved": True, "new_components_consistent": True, "uninstall": "PASS"}
    finally:
        if old_server.poll() is None:
            old_server.kill()
            old_server.wait(10)
        for stream in (old_server.stdin, old_server.stdout, old_server.stderr):
            stream.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=Path)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Use only on a disposable GitHub runner; this test writes the real project AppId")
    with tempfile.TemporaryDirectory(prefix="actilo-legacy-matrix-", ignore_cleanup_errors=True) as directory:
        rows = [check(args.installer.resolve(), args.payload.resolve(), Path(directory) / version, version) for version in ("1.5.0", "1.6.0", "1.7.0")]
    args.output.write_text(json.dumps({"version": "1.9.10", "actual_legacy_installers": rows}, indent=2), encoding="utf-8")
    print(json.dumps({"actual_legacy_versions": [row["from_version"] for row in rows], "migration": "PASS"}))


if __name__ == "__main__":
    main()
