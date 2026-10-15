"""Explicit portable bundle paths; never infer storage from the working directory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PORTABLE_MARKER = "WindowsMCP.portable.json"
PORTABLE_MODE = {"schema": 1, "mode": "portable"}


def portable_data_directory(executable: Path | None = None, *, frozen: bool | None = None) -> Path | None:
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    if not frozen:
        return None
    executable = Path(sys.executable) if executable is None else executable
    directory = executable.absolute().parent
    candidates = [directory]
    # The manager copies its service into data/Windows-MCP.exe. Both entry
    # points must use the same config, approval authority and protected data.
    if directory.name.casefold() == "data":
        candidates.append(directory.parent)
    for root in candidates:
        marker = root / PORTABLE_MARKER
        if not marker.exists() and not marker.is_symlink():
            continue
        data = root / "data"
        for path in (marker, data, *data.parents):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise PermissionError("Portable storage cannot use links or junctions")
        if marker.stat().st_size > 1024 or json.loads(marker.read_text(encoding="utf-8")) != PORTABLE_MODE:
            raise ValueError("Invalid Windows-MCP portable marker; extract the complete official ZIP")
        # Expand Win32 8.3 aliases (for example RUNNER~1) after rejecting
        # links. Ownership and scope checks compare canonical user paths.
        return data.resolve()
    return None


def configuration_directory() -> Path:
    data = portable_data_directory()
    return data / "config" if data is not None else Path.home() / ".windows-mcp"
