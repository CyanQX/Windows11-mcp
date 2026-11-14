"""Names and strict structure checks shared by packaging and verified updates."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

from windows_mcp.infrastructure.runtime_paths import PORTABLE_MARKER, PORTABLE_MODE

MAX_PAYLOAD = 600 * 1024 * 1024


def setup_name(version):
    from windows_mcp.product import installer_name
    return installer_name(version)


def portable_name(version):
    return f"Windows-MCP-Portable-v{version}-x64.zip"


def portable_bundle_name(version):
    return f"Windows-MCP-PortableBundle-v{version}-x64.zip"


def portable_root(version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)", version):
        raise ValueError("Invalid portable version")
    return f"Windows-MCP-Portable-v{version}-x64"


def verify_portable(path: Path, version: str) -> dict:
    from installer.storage import check_path
    path = check_path(path)
    root = portable_root(version)
    required = {f"{root}/{name}" for name in ("Windows-MCP.exe", PORTABLE_MARKER, "README.txt", "LICENSE.txt", "THIRD-PARTY-LICENSES.txt")}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        from installer.bundle import BUNDLE_FILE, MAX_UNPACKED, validate
        manifest_entry = f"{root}/{BUNDLE_FILE}"
        bundle = None
        if manifest_entry in names:
            if archive.getinfo(manifest_entry).file_size > 2 * 1024 * 1024:
                raise ValueError("Portable bundle manifest exceeds the size limit")
            bundle = validate(json.loads(archive.read(manifest_entry)))
            if bundle.get("version") != version:
                raise ValueError("Portable bundle version disagrees")
            required |= {manifest_entry, *(f"{root}/{name}" for name in bundle["files"])}
        if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}) or set(names) != required | {f"{root}/data/"}:
            raise ValueError("Portable archive contains missing, duplicate or unexpected entries")
        if sum(row.file_size for row in archive.infolist()) > (MAX_UNPACKED if bundle else MAX_PAYLOAD):
            raise ValueError("Portable archive exceeds the size limit")
        payload_names = {f"{root}/{name}" for name in bundle["files"]} if bundle else {f"{root}/Windows-MCP.exe"}
        for row in archive.infolist():
            if row.filename not in payload_names and row.file_size > 2 * 1024 * 1024:
                raise ValueError("Portable metadata exceeds the size limit")
        if archive.testzip() is not None:
            raise ValueError("Portable archive CRC failed")
        if json.loads(archive.read(f"{root}/{PORTABLE_MARKER}")) != PORTABLE_MODE:
            raise ValueError("Portable marker is invalid")
        payload = archive.read(f"{root}/Windows-MCP.exe")
        if payload[:2] != b"MZ":
            raise ValueError("Portable executable is invalid")
        if bundle:
            for name, expected in bundle["files"].items():
                entry = archive.getinfo(f"{root}/{name}")
                if entry.file_size != expected["size"]:
                    raise ValueError("Portable bundle size mismatch")
                with archive.open(entry) as source:
                    actual = hashlib.file_digest(source, "sha256").hexdigest()
                if actual != expected["sha256"]:
                    raise ValueError("Portable bundle fingerprint mismatch")
    return {"version": version, "archive": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "payload_entry": f"{root}/Windows-MCP.exe", "payload_sha256": hashlib.sha256(payload).hexdigest(), "entries": len(names), "portable_structure_verified": True, "bundle": bundle}
