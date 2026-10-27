"""Build a self-contained Windows GUI and MCP stdio executable."""

import argparse
import json
import os
import sys
import tomllib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "payload")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "setup")
    parser.add_argument("--with-capture", action="store_true", help="Bundle the optional HWND WGC component")
    parser.add_argument("--with-ocr", action="store_true", help="Bundle optional OCR bindings; an engine/package identity is still required")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows EXE must be built on Windows")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == version
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["WINDOWS_MCP_WATCHDOG"] = "off"
    os.environ["WINDOWS_MCP_DISABLE_FLASH"] = "1"
    # Bindepend must not resolve Windows ICU against unrelated tools (for
    # example Poppler/Conda) on the caller's PATH. Qt uses the Windows ICU ABI;
    # a third-party icuuc.dll can have incompatible version-suffixed exports.
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    os.environ["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows]))
    import PyInstaller.__main__

    args.work.mkdir(parents=True, exist_ok=True)
    options = [str(ROOT / "installer" / "app.py"), "--onefile", "--windowed", "--clean", "--noconfirm", "--name", "Windows-MCP", "--distpath", str(args.out), "--workpath", str(args.work / "analysis"), "--specpath", str(args.work), "--paths", str(ROOT)]
    for module in ("windows_mcp", "fastmcp", "playwright", "comtypes"):
        options += ["--collect-all", module]
    for enabled, modules in ((args.with_capture, ("windows_capture",)),
                              (args.with_ocr, ("pytesseract", "winrt"))):
        for module in modules:
            if enabled:
                if importlib.util.find_spec(module) is None:
                    parser.error(f"Install optional dependency {module} before including it")
                options += ["--collect-all", module]
            else:
                options += ["--exclude-module", module]
    for module in ("mcp.client", "mcp.server", "mcp.shared"):
        options += ["--collect-submodules", module]
    options += ["--collect-data", "mcp", "--exclude-module", "mcp.cli"]
    options += ["--recursive-copy-metadata", "windows-mcp"]
    options += ["--copy-metadata", "pyside6-essentials", "--copy-metadata", "shiboken6"]
    for module in ("tomlkit", "ruamel.yaml", "json5"):
        options += ["--copy-metadata", module]
    for source, destination in ((ROOT / "manifest.json", "."), (ROOT / "LICENSE.md", "."), (ROOT / "installer" / "resources", "installer/resources"), (ROOT / "skills" / "windows-mcp", "skills/windows-mcp")):
        options += ["--add-data", f"{source}{os.pathsep}{destination}"]
    PyInstaller.__main__.run(options)
    # Verify actual packed code, including installer modules omitted by the wheel.
    # A version label alone cannot detect stale PyInstaller module caches.
    sys.path.insert(0, str(ROOT))
    from scripts.verify_setup_source import verify
    result = verify(args.out / "Windows-MCP.exe")
    (args.out / "setup-source-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
