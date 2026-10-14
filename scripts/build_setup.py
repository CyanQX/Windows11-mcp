"""Build a self-contained Windows GUI and MCP stdio executable."""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "setup")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "setup")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows EXE must be built on Windows")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == version
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["WINDOWS_MCP_WATCHDOG"] = "off"
    os.environ["WINDOWS_MCP_DISABLE_FLASH"] = "1"
    import PyInstaller.__main__

    args.work.mkdir(parents=True, exist_ok=True)
    options = [str(ROOT / "installer" / "app.py"), "--onefile", "--windowed", "--noconfirm", "--name", f"Windows-MCP-Setup-{version}", "--distpath", str(args.out), "--workpath", str(args.work / "analysis"), "--specpath", str(args.work), "--paths", str(ROOT)]
    for module in ("windows_mcp", "fastmcp", "playwright", "customtkinter", "comtypes"):
        options += ["--collect-all", module]
    for module in ("mcp.client", "mcp.server", "mcp.shared"):
        options += ["--collect-submodules", module]
    options += ["--collect-data", "mcp", "--exclude-module", "mcp.cli"]
    options += ["--recursive-copy-metadata", "windows-mcp"]
    options += ["--recursive-copy-metadata", "customtkinter"]
    for module in ("tomlkit", "ruamel.yaml", "json5"):
        options += ["--copy-metadata", module]
    for source, destination in ((ROOT / "manifest.json", "."), (ROOT / "LICENSE.md", "."), (ROOT / "skills" / "windows-mcp", "skills/windows-mcp")):
        options += ["--add-data", f"{source}{os.pathsep}{destination}"]
    PyInstaller.__main__.run(options)


if __name__ == "__main__":
    main()
