"""Scoped text files, with read-back verification and no silent overwrite."""

from __future__ import annotations

import os
import tempfile
import logging
from pathlib import Path

from windows_mcp.engine.contracts import ActionResult, Status, Verification

logger = logging.getLogger(__name__)


def replace_verified(temporary: Path, destination: Path) -> None:
    """Preserve an existing Windows file's DACL/encryption when publishing new content."""
    if os.name != "nt" or not destination.exists():
        os.replace(temporary, destination)
        return
    import ctypes
    from ctypes import wintypes

    backup = temporary.with_suffix(".previous")
    if backup.exists():
        raise FileExistsError("Atomic replacement recovery path already exists")
    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    # Keep a same-volume recovery copy for Windows' documented partial-failure cases.
    names = ["\\\\?\\" + str(path) for path in (destination, temporary, backup)]
    if not replace(*names, 0, None, None):
        error = ctypes.WinError(ctypes.get_last_error())
        if backup.exists() and not destination.exists():
            try:
                os.replace(backup, destination)
            except OSError as recovery_error:
                raise OSError(f"Replacement failed; original recovery copy remains at {backup}") from recovery_error
        raise error
    try:
        backup.unlink(missing_ok=True)
    except OSError:
        logger.warning("Atomic replacement succeeded but its original recovery file could not be removed")


def run(policy, operation: str, path: str, *, content: str | None = None, overwrite: bool = False) -> dict:
    destination = policy.check_path(path)
    if operation == "read":
        if destination.stat().st_size > 1_000_000:
            raise ValueError("Native text reads are limited to 1 MB")
        return {"source": "native", "text": destination.read_text(encoding="utf-8")}
    if operation == "info":
        return {"source": "native", "exists": destination.exists(), "is_file": destination.is_file(),
                "size": destination.stat().st_size if destination.exists() else None}
    if operation != "write" or content is None:
        raise ValueError("Native filesystem supports read, info, write; write requires content")
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Native text writes are limited to 1 MB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content.encode("utf-8"))
                file.flush()
                os.fsync(file.fileno())
            if temporary.read_bytes() != content.encode("utf-8"):
                raise OSError("Temporary file verification failed; original file was not replaced")
            if policy.check_path(destination) != destination or policy.check_path(temporary) != temporary:
                raise PermissionError("File path changed before replacement")
            replace_verified(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        with destination.open("x", encoding="utf-8", newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    passed = destination.read_bytes().decode("utf-8") == content
    return ActionResult(status=Status.VERIFIED if passed else Status.DISPATCHED_UNVERIFIED,
                        method="native_filesystem_write", precondition_passed=True, input_dispatched=True,
                        verification=Verification(passed=passed, evidence_type="file_content", detail="Written file content was independently read back")).model_dump()
