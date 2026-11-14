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

MANIFEST = "WindowsMCP.bundle.json"


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

