"""Build a self-contained Windows GUI and MCP stdio executable."""

import argparse
import json
import os
import sys
import tomllib
import importlib.util
import hashlib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "payload")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "setup")
    parser.add_argument("--with-capture", action="store_true", help="Bundle the optional HWND WGC component")
    parser.add_argument("--with-ocr", action="store_true", help="Bundle optional OCR bindings; an engine/package identity is still required")
    parser.add_argument("--compat-out", type=Path, help="Also build the one-time bridge used by older portable updaters")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows EXE must be built on Windows")
    import struct
    if struct.calcsize("P") != 8:
        parser.error("The x64 installer requires a 64-bit Python build interpreter")
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
    sys.path.insert(0, str(ROOT))
    from installer.bundle import source_id, create

    # Two builds of identical source may collect different OS-provided DLLs.
    # Never reuse an immutable support directory across distinct build outputs.
    identity = source_id(ROOT, version, sys.version) + uuid.uuid4().hex
    contents = "_runtime-" + hashlib.sha256(identity.encode()).hexdigest()[:20]

    args.work.mkdir(parents=True, exist_ok=True)
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
    from windows_mcp.product import NAME, EXECUTABLE, release_version
    assert release_version() == version, "Installed build metadata differs from pyproject.toml"
    number = tuple(map(int, version.split("."))) + (0,)
    info = VSVersionInfo(ffi=FixedFileInfo(filevers=number, prodvers=number, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=[
        StringFileInfo([StringTable("040904B0", [StringStruct(key, value) for key, value in {
            "CompanyName": "CyanQX", "FileDescription": NAME, "FileVersion": version,
            "InternalName": NAME, "OriginalFilename": EXECUTABLE, "ProductName": NAME,
            "ProductVersion": version, "LegalCopyright": "MIT; upstream and third-party notices retained"}.items()])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])])])
    version_file = args.work / "product-version.txt"
    version_file.write_text(str(info), encoding="utf-8")
    options = [str(ROOT / "installer" / "app.py"), "--onedir", "--contents-directory", contents, "--windowed", "--clean", "--noconfirm", "--name", "Windows-MCP", "--distpath", str(args.out), "--workpath", str(args.work / "analysis"), "--specpath", str(args.work), "--paths", str(ROOT)]
    options += ["--collect-submodules", "installer"]
    options += ["--version-file", str(version_file)]
    options += ["--icon", str(ROOT / "installer/resources/actilo.ico")]
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
    bundle_root = args.out / "Windows-MCP"
    create(bundle_root, contents, version)
    # Verify actual packed code, including installer modules omitted by the wheel.
    # A version label alone cannot detect stale PyInstaller module caches.
    sys.path.insert(0, str(ROOT))
    from scripts.verify_setup_source import verify
    result = verify(bundle_root / "Windows-MCP.exe")
    (args.out / "setup-source-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.compat_out:
        from scripts.build_compat import build
        build(bundle_root, args.compat_out, args.work / "compat-native")


if __name__ == "__main__":
    main()
