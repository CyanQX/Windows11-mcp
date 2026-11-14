"""One-time, offline migration for pre-1.8 single-file portable updaters.

Only this compatibility download extracts a payload. It replaces itself with
the verified small executable; subsequent starts use the persistent directory.
Keep this module standard-library-only so the bridge has a small Python image.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import uuid
import struct
import zlib

MANIFEST = "WindowsMCP.bundle.json"


def installed_version(executable):
    """Read bounded metadata, never execute the old image to learn its version."""
    executable = safe(executable)
    bundle = executable.parent / MANIFEST
    if bundle.is_file() and bundle.stat().st_size <= 2 * 1048576:
        record = json.loads(bundle.read_text(encoding="utf-8"))
        if record.get("schema") == 1 and record.get("files", {}).get("Windows-MCP.exe", {}).get("sha256") == digest(executable):
            return record.get("version")
        return None
    # Standard PyInstaller CArchive layout; inspect only the JSON resource.
    # A footer plus strict offsets prevents treating arbitrary bytes as a plan.
    with executable.open("rb") as stream:
        size = executable.stat().st_size
        start = max(0, size - 1048576)
        stream.seek(start)
        tail = stream.read()
        offset = tail.rfind(b"MEI\x0c\x0b\x0a\x0b\x0e")
        if offset < 0 or offset + 88 > len(tail):
            return None
        _, length, toc_offset, toc_length, _, _ = struct.unpack("!8sIIII64s", tail[offset:offset + 88])
        base = start + offset + 88 - length
        if base < 0 or not 0 < toc_length <= 16 * 1048576 or toc_offset + toc_length > length - 88:
            return None
        stream.seek(base + toc_offset)
        toc = stream.read(toc_length)
        cursor = 0
        while cursor + 18 <= len(toc):
            entry, position, packed, unpacked, compressed, _ = struct.unpack("!IIIIBc", toc[cursor:cursor + 18])
            if entry <= 18 or cursor + entry > len(toc):
                return None
            name = toc[cursor + 18:cursor + entry].rstrip(b"\0")
            cursor += entry
            if name != b"manifest.json":
                continue
            if not 0 < packed <= 2 * 1048576 or not 0 < unpacked <= 2 * 1048576 or position + packed > toc_offset:
                return None
            stream.seek(base + position)
            data = stream.read(packed)
            if compressed:
                inflater = zlib.decompressobj()
                data = inflater.decompress(data, 2 * 1048576 + 1)
                if not inflater.eof or inflater.unconsumed_tail:
                    return None
            if len(data) != unpacked:
                return None
            return json.loads(data.decode("utf-8")).get("version")
    return None


def manual_upgrade_guide():
    import ctypes
    text = ("发现重大版本更新：Actilo v1.9.10\n当前版本低于 v1.7.0 或无法可靠识别。"
            "为确保迁移稳定，不执行静默原地更新。请前往 GitHub 下载 Actilo_1.9.10_setup.exe 并手动安装。"
            "\n选择“是”前往 GitHub 下载；选择“否”稍后再说。")
    if ctypes.windll.user32.MessageBoxW(None, text, "Actilo · 重大版本更新", 0x24) == 6:
        import webbrowser
        webbrowser.open("https://github.com/CyanQX/Windows11-mcp/releases/latest")


def safe(path, root=None):
    path = Path(path).absolute()
    if ".." in path.parts or path.drive.startswith("\\\\"):
        raise ValueError("Unsafe migration path")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Migration path contains a reparse point")
    if root is not None and not path.is_relative_to(Path(root).absolute()):
        raise ValueError("Migration path escapes its directory")
    return path


def digest(path):
    with safe(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path = safe(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize(source, target):
    source, target = safe(source), safe(target)
    record = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    contents = record.get("contents", "")
    if record.get("schema") != 1 or not re.fullmatch(r"_runtime-[a-f0-9]{20}", contents):
        raise ValueError("Invalid embedded migration payload")
    files = record["files"]
    if not 2 <= len(files) <= 6000 or "Windows-MCP.exe" not in files:
        raise ValueError("Incomplete migration payload")
    seen = set()
    for name in sorted(files, key=lambda name: name == "Windows-MCP.exe"):
        expected = files[name]
        parts = PurePosixPath(name).parts
        if (not parts or parts[0] not in {contents, "Windows-MCP.exe"}
                or (parts[0] == "Windows-MCP.exe" and len(parts) != 1)
                or any(part in {".", ".."} for part in parts)
                or "\\" in name or ":" in name or PurePosixPath(name).as_posix() != name
                or name.casefold() in seen):
            raise ValueError("Invalid embedded payload path")
        seen.add(name.casefold())
        original = safe(source / name, source)
        if original.stat().st_size != expected["size"] or digest(original) != expected["sha256"]:
            raise ValueError("Embedded payload fingerprint failed")
        destination = safe(target / name, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if digest(destination) == expected["sha256"]:
                continue
            if name != "Windows-MCP.exe":
                raise ValueError("Existing immutable runtime differs")
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            shutil.copyfile(original, temporary)
            if digest(temporary) != expected["sha256"]:
                raise ValueError("Migration copy fingerprint failed")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    write_json(target / MANIFEST, record)
    return record


def migrate(executable, source, arguments, launch=subprocess.Popen):
    executable = safe(executable)
    plan_argument = next((arg.split("=", 1)[1] for arg in arguments if arg.startswith("--apply-update=")), None)
    plan, row = None, None
    if plan_argument:
        plan = safe(plan_argument)
        row = json.loads(plan.read_text(encoding="utf-8"))
        nonce = next((arg.split("=", 1)[1] for arg in arguments if arg.startswith("--update-nonce=")), "")
        root = safe(Path(row["data"]) / "updates")
        safe(plan, root)
        target = safe(row["target"])
        if (not re.fullmatch(r"[a-f0-9]{32}", nonce) or row.get("nonce") != nonce
                or not 0 <= time.time() - row["created"] < 3600
                or safe(row["staged"]) != executable or executable != plan.parent / "next.exe"
                or safe(row["backup"]) != plan.parent / "previous.exe"
                or target.is_relative_to(root) or target.suffix.lower() != ".exe"
                or digest(executable) != row["sha256"] or digest(target) != row["old_sha256"]):
            raise ValueError("Legacy migration plan is invalid")
        try:
            version = installed_version(target)
            parts = tuple(map(int, version.split("."))) if version and re.fullmatch(r"\d+\.\d+\.\d+", version) else ()
        except (OSError, ValueError, struct.error, zlib.error):
            parts = ()
        if not parts or parts < (1, 7, 0):
            write_json(plan.parent / "result.json", {"ok": False, "manual_upgrade_required": True,
                "error": "MANUAL_UPGRADE_REQUIRED", "url": "https://github.com/CyanQX/Windows11-mcp/releases/latest"})
            manual_upgrade_guide()
            return 1
    else:
        marker = executable.parent / "WindowsMCP.portable.json"
        if json.loads(marker.read_text(encoding="utf-8")) != {"schema": 1, "mode": "portable"}:
            raise ValueError("Extract the complete portable archive before launching")
    # Windows permits renaming a loaded image. Never overwrite or delete it.
    retired = executable.with_name("legacy-bootstrap-" + uuid.uuid4().hex + ".exe")
    os.rename(executable, retired)
    try:
        record = materialize(source, executable.parent)
        core = executable.parent / "Windows-MCP.exe"
        if plan:
            shutil.copyfile(core, executable)
            row.update(sha256=record["files"]["Windows-MCP.exe"]["sha256"], bundle_sha256=digest(executable.parent / MANIFEST))
            write_json(plan, row)
            core = executable
    except Exception:
        if not executable.exists():
            os.rename(retired, executable)
        raise
    env = {name: value for name, value in os.environ.items() if not name.startswith("_PYI_") and name != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    child = launch([str(core), *arguments], env=env, close_fds=True)
    if not plan and arguments:
        return child.wait()
    return 0

