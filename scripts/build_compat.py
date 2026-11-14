"""Build the transparent .NET Framework migration launcher; no one-file packer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(source: Path, output: Path, work: Path):
    from installer.bundle import BUNDLE_FILE, verify
    record = verify(source)
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        raise RuntimeError("The Windows .NET Framework compiler is required for legacy migration builds")
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    archive = work / "payload.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name in [BUNDLE_FILE, *record["files"]]:
            package.write(source / name, name)
    pin = hashlib.sha256((source / BUNDLE_FILE).read_bytes()).hexdigest()
    constant = work / "PayloadFingerprint.cs"
    constant.write_text('internal static partial class Bootstrap { const string ManifestHash = "' + pin + '"; }', encoding="utf-8")
    target = output / "Windows-MCP.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/debug-",
                    "/reference:System.IO.Compression.dll", "/reference:System.Web.Extensions.dll",
                    "/win32manifest:" + str(ROOT / "installer/compat/Bootstrap.manifest"),
                    "/out:" + str(target.resolve()), "/resource:" + str(archive.resolve()) + ",payload.zip",
                    str(ROOT / "installer/compat/Bootstrap.cs"), str(constant.resolve())], check=True)
    result = {"engine": "Windows .NET Framework", "onefile_python": False, "manifest_sha256": pin,
              "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "files_verified": len(record["files"])}
    (output / "legacy-migration-build.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
