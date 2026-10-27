"""Operator-issued, session-bound HTTP origin grants, independent of R2 trust."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from windows_mcp.infrastructure.approvals import read_signed_record, sign_record
from windows_mcp.infrastructure.local_storage import FileLock, private_directory


def origin(url: str) -> str:
    """Normalize an exact HTTP origin without DNS or network access."""
    if not isinstance(url, str) or len(url) > 8192 or any(ord(char) <= 32 or char == "\\" for char in url):
        raise ValueError("URL must be HTTP(S), at most 8192 characters, without whitespace/control characters")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Only HTTP(S) URLs without embedded credentials are supported")
    host = parsed.hostname.rstrip(".")
    try:
        address = ipaddress.ip_address(host)
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    except ValueError:
        host = host.encode("idna").decode("ascii").lower()
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
            raise ValueError("Invalid HTTP hostname")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid HTTP port")
    suffix = f":{port}" if port is not None and port != (443 if parsed.scheme == "https" else 80) else ""
    return f"{parsed.scheme}://{host}{suffix}"


class NetworkGrants:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.path = self.directory / "network-grants.json"

    def status(self, session_id: str | None = None) -> list[dict]:
        payload = read_signed_record(self.directory, self.path)
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            return []
        rows = payload.get("grants", [])
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict) and isinstance(row.get("expires_at"), (int, float))
                and row["expires_at"] > time.time() and (session_id is None or row.get("session_id") == session_id)]

    def allows(self, session_id: str, url: str) -> bool:
        target = origin(url)
        return any(row.get("origin") == target for row in self.status(session_id))

    def grant(self, session_id: str, origins: list[str], *, ttl: float = 8 * 3600) -> None:
        if not session_id or not 0 < ttl <= 8 * 3600:
            raise ValueError("Network grants require a session and TTL of at most 8 hours")
        normalized = {origin(value) for value in origins}
        private_directory(self.directory)
        with FileLock(self.directory / ".network.lock", timeout=2):
            rows = [row for row in self.status() if not (row.get("session_id") == session_id and row.get("origin") in normalized)]
            rows.extend({"session_id": session_id, "origin": value, "expires_at": time.time() + ttl} for value in normalized)
            if len(rows) > 256:
                raise ValueError("Network authorization capacity reached; revoke unused grants")
            self._save(rows)

    def revoke(self, session_id: str | None = None) -> None:
        private_directory(self.directory)
        with FileLock(self.directory / ".network.lock", timeout=2):
            self._save([row for row in self.status() if session_id is not None and row.get("session_id") != session_id])

    def _save(self, rows: list[dict]) -> None:
        temporary = self.path.with_suffix(f".{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_text(json.dumps(sign_record(self.directory, {"schema": 1, "grants": rows}), ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
