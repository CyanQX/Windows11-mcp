"""Verified one-folder payloads with immutable, versioned support directories.

The small executable keeps its stable client path. A new support directory is
fully staged before replacing that executable; old processes and rollback images
can keep using the previous directory without sharing a temporary _MEI lifetime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath

BUNDLE_FILE = "WindowsMCP.bundle.json"
MAX_UNPACKED = 1500 * 1024 * 1024
MAX_FILES = 6000


def validate(record: dict) -> dict:
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError("Invalid runtime bundle manifest")
    contents = record.get("contents", "")
    if not isinstance(contents, str) or not re.fullmatch(r"_runtime-[a-f0-9]{20}", contents):
        raise ValueError("Invalid runtime support directory")
    files = record.get("files")
    if not isinstance(files, dict) or not 2 <= len(files) <= MAX_FILES or "Windows-MCP.exe" not in files:
        raise ValueError("Incomplete runtime bundle")
    seen, total = set(), 0
    for name, item in files.items():
        if not isinstance(name, str) or "\\" in name or ":" in name or len(name) > 240:
            raise ValueError("Invalid runtime bundle path")
        parts = PurePosixPath(name).parts
        if not parts or parts[0] not in {"Windows-MCP.exe", contents} or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Runtime bundle path escapes its directory")
        if PurePosixPath(name).as_posix() != name or name.casefold() in seen:
            raise ValueError("Duplicate or ambiguous runtime bundle path")
        if name != "Windows-MCP.exe" and (len(parts) < 2 or parts[0] != contents):
            raise ValueError("Unexpected runtime bundle entry")
        seen.add(name.casefold())
        if (not isinstance(item, dict) or type(item.get("size")) is not int or item["size"] < 0
                or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
            raise ValueError("Invalid runtime bundle fingerprint")
        total += item["size"]
    if total > MAX_UNPACKED:
        raise ValueError("Runtime bundle exceeds the size limit")
    return record


def load(root: Path) -> dict | None:
    from installer.storage import check_path

    path = check_path(root / BUNDLE_FILE, root)
    if not path.is_file():
        return None  # Legacy one-file distributions remain maintainable.
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Runtime bundle manifest is too large")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def verify(root: Path, record: dict | None = None, *, executable=True) -> dict:
    from installer.storage import check_path, file_hash, hash_snapshot, snapshot_cache

    record = validate(record) if record is not None else load(root)
    if record is None:
        raise ValueError("Runtime bundle manifest is missing")
    with hash_snapshot():
        root = check_path(root)
        cache = snapshot_cache()
        key = ("verified-bundle", str(root), executable, hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest())
        def stamp(path):
            state = path.lstat()
            identity = (state.st_ino, state.st_ctime_ns, getattr(state, "st_file_attributes", 0), getattr(state, "st_reparse_tag", 0))
            return identity if path.is_dir() else (*identity, state.st_size, state.st_mtime_ns)
        previous = cache.get(key)
        if previous is not None:
            try:
                if all(stamp(path) == observed for path, observed in previous):
                    return record
            except OSError:
                pass
        observed_paths = {root}
        for name in record["files"]:
            if name == "Windows-MCP.exe" and not executable:
                continue
            path = check_path(root / name, root)
            observed_paths.add(path)
            observed_paths.update(parent for parent in path.parents if parent.is_relative_to(root))
        observed = [(path, stamp(path)) for path in observed_paths]
        for name, item in record["files"].items():
            if name == "Windows-MCP.exe" and not executable:
                continue
            path = check_path(root / name, root)
            if not path.is_file() or path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]:
                raise ValueError("Runtime bundle file failed verification: " + name)
        if any(stamp(path) != state for path, state in observed):
            raise ValueError("Runtime bundle changed during verification")
        cache[key] = observed
    return record


def create(root: Path, contents: str, version: str) -> dict:
    from installer.storage import check_path, file_hash

    paths = [root / "Windows-MCP.exe", *sorted((root / contents).rglob("*"))]
    files = {}
    for path in paths:
        check_path(path, root)
        if path.is_file():
            files[path.relative_to(root).as_posix()] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    record = validate({"schema": 1, "version": version, "contents": contents, "files": files})
    from installer.backend import atomic_write

    atomic_write(root / BUNDLE_FILE, json.dumps(record, sort_keys=True, indent=2).encode("utf-8"))
    return record


def source_id(root: Path, version: str, python_version: str) -> str:
    value = hashlib.sha256((version + python_version).encode())
    for name in ("pyproject.toml", "uv.lock", "manifest.json"):
        value.update((root / name).read_bytes())
    for directory in ("src/windows_mcp", "installer", "skills/windows-mcp"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                value.update(path.relative_to(root).as_posix().encode())
                value.update(path.read_bytes())
    return value.hexdigest()[:20]


def stage_support(source: Path, target: Path, record: dict) -> None:
    from installer.storage import check_path

    record = validate(record)
    target = check_path(target)
    destination = check_path(target / record["contents"], target)
    if source.resolve() == target.resolve() or destination.exists():
        verify(target, record, executable=False)
        return
    target.mkdir(parents=True, exist_ok=True)
    temporary = check_path(target / (".bundle-" + uuid.uuid4().hex), target)
    try:
        for name in record["files"]:
            if name == "Windows-MCP.exe":
                continue
            source_file = check_path(source / name, source)
            copied = check_path(temporary / name, temporary)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, copied)
        verify(temporary, record, executable=False)
        os.rename(temporary / record["contents"], destination)
    finally:
        # The generated staging directory has never been launched or exposed.
        checked = check_path(temporary, target)
        if checked.parent != target or not re.fullmatch(r"\.bundle-[a-f0-9]{32}", checked.name):
            raise ValueError("Unsafe runtime staging cleanup")
        if checked.exists():
            shutil.rmtree(checked)
