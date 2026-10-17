"""Operator-minted trust grant: one explicit local decision can cover later R2 calls.

Only the local manager (or the ``windows-mcp trust`` CLI) can mint a grant. The
record is HMAC-signed with the approval authority key and stored beside the
approvals, outside the tool-accessible workspace, so no MCP tool can create,
extend or tamper with it. Revoking the grant restores per-call approvals.

Trust never widens the R3 boundary: PowerShell, Registry writes, Process kills
and similar R3 tools stay disabled unless the operator enables ``policy.allow_r3``
in the local server configuration.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from windows_mcp.infrastructure.approvals import read_signed_record, sign_record
from windows_mcp.infrastructure.local_storage import FileLock, private_directory

TRUST_FILE = "operator-trust.json"
MODES = {"session", "forever"}
DEFAULT_SESSION_TTL = 8 * 3600.0


class TrustStore:
    def __init__(self, directory: Path, *, session_ttl: float = DEFAULT_SESSION_TTL) -> None:
        self.directory = directory.resolve()
        self.session_ttl = session_ttl
        self.path = self.directory / TRUST_FILE
        self._lock_file = self.directory / ".trust.lock"

    @contextmanager
    def _exclusive(self):
        private_directory(self.directory)
        with FileLock(self._lock_file, timeout=2):
            yield

    def status(self) -> dict | None:
        """Return the verified, unexpired grant payload, or ``None``."""
        payload = read_signed_record(self.directory, self.path)
        if payload is None:
            return None
        if payload.get("mode") not in MODES:
            return None
        expires_at = payload.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, (int, float)):
            return None
        if expires_at is not None and time.time() >= expires_at:
            return None
        return payload

    def allows(self) -> bool:
        return self.status() is not None

    def grant(self, mode: str = "session", *, operator: str = "local-ui") -> dict:
        if mode not in MODES:
            raise ValueError(f"Unknown trust mode: {mode!r}")
        now = time.time()
        payload = {
            "schema": 1,
            "mode": mode,
            "granted_at": now,
            "expires_at": None if mode == "forever" else now + self.session_ttl,
            "operator": operator,
        }
        with self._exclusive():
            path = self.path
            temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
            temporary.write_text(json.dumps(sign_record(self.directory, payload), ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        return payload

    def revoke(self) -> None:
        with self._exclusive():
            self.path.unlink(missing_ok=True)
