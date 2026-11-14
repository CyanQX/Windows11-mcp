"""Build the transparent .NET Framework migration launcher; no one-file packer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def build(source: Path, output: Path, work: Path):
    from installer.bundle import BUNDLE_FILE, verify
    from windows_mcp.product import NAME, release_version
    record = verify(source)
    version = release_version()
    if record.get("version") != version:
        raise ValueError("Compatibility payload and product versions differ")
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
    metadata = work / "ProductMetadata.cs"
    metadata.write_text('using System.Reflection;\n' + '\n'.join(
        f'[assembly: {key}("{value}")]' for key, value in {
            "AssemblyTitle": NAME, "AssemblyProduct": NAME, "AssemblyCompany": "CyanQX",
            "AssemblyDescription": NAME + " legacy migration launcher",
            "AssemblyVersion": version + ".0", "AssemblyFileVersion": version + ".0",
            "AssemblyInformationalVersion": version,
        }.items()), encoding="utf-8")
    manifest = ET.parse(ROOT / "installer/compat/Bootstrap.manifest")
    identity = manifest.find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity")
    identity.set("version", version + ".0")
    identity.set("name", NAME + ".LegacyMigration")
    manifest_path = work / "Bootstrap.manifest"
    manifest.write(manifest_path, encoding="utf-8", xml_declaration=True)
    target = output / "Windows-MCP.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/debug-",
                    "/reference:System.IO.Compression.dll", "/reference:System.Web.Extensions.dll",
                    "/win32manifest:" + str(manifest_path.resolve()),
                    "/win32icon:" + str(ROOT / "installer/resources/actilo.ico"),
                    "/out:" + str(target.resolve()), "/resource:" + str(archive.resolve()) + ",payload.zip",
                    str(ROOT / "installer/compat/Bootstrap.cs"), str(constant.resolve()), str(metadata.resolve())], check=True)
    result = {"engine": "Windows .NET Framework", "onefile_python": False, "manifest_sha256": pin,
              "product": NAME, "version": version, "file_version": version + ".0",
              "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "files_verified": len(record["files"])}
    (output / "legacy-migration-build.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
