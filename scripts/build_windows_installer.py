"""Compile a real per-user installation wizard with location and uninstall support."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "dist/setup")
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    compiler = args.iscc or shutil.which("ISCC.exe") or Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe"
    if not Path(compiler).is_file():
        parser.error("Install Inno Setup 6 or provide --iscc. GitHub windows-latest includes the compiler.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    args.out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/Qp", f"/DAppVersion={version}", f"/DPayloadPath={args.executable.resolve()}", f"/DRepositoryRoot={ROOT}", f"/DOutputDir={args.out.resolve()}", str(ROOT / "installer/windows-mcp.iss")], check=True)
    target = args.out / f"Windows-MCP-Setup-v{version}-x64.exe"
    result = {"version": version, "installer": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "payload_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(), "installer_engine": "Inno Setup", "per_user": True, "directory_selection": True, "system_uninstaller": True}
    (args.out / "installer-build.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
