"""Operator-minted persistent installation preference and legacy scoped grants.

Only the local manager (or the ``windows-mcp trust`` CLI) can mint a grant. The
record is HMAC-signed with the approval authority key and stored beside the
approvals, outside the tool-accessible workspace, so no MCP tool can create,
extend or tamper with it. Revoking the grant restores per-call approvals.

Schema 4 explicitly permits full access under the Windows account until revoked.
Schema 3 enables installation-wide automatic execution with configured limits;
schema 2 retains its exact session/application/tool/action scope and lifetime.
Unsigned or schema 1 global records do not enable either mode. Trust never enables
disabled capabilities: R3 tools still require local ``policy.allow_r3`` configuration.
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
        if not isinstance(payload, dict):
            return None
        if payload.get("schema") in {3, 4}:
            if (payload.get("mode") != "automatic" or payload.get("scope") != "installation"
                    or payload.get("expires_at") is not None or payload.get("max_risk") != 3
                    or payload.get("network") is not True or payload.get("tools") != ["*"]):
                return None
            if payload["schema"] == 4 and payload.get("access") != "full":
                return None
            return payload
        if payload.get("schema") != 2:
            return None
        if not isinstance(payload.get("session_id"), str) or not payload["session_id"] or not payload.get("applications") or not payload.get("tools"):
            return None
        if payload.get("mode") not in MODES:
            return None
        expires_at = payload.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, (int, float)):
            return None
        if expires_at is not None and time.time() >= expires_at:
            return None
        return payload

    @staticmethod
    def automatic_tools() -> list[str]:
        from windows_mcp.infrastructure.authorization import TRUST_TOOLS

        return sorted(TRUST_TOOLS | {"PowerShell", "Registry", "Process"})

    def automatic(self) -> bool:
        payload = self.status()
        return bool(payload and payload.get("schema") in {3, 4})

    def full_access(self) -> bool:
        payload = self.status()
        return bool(payload and payload.get("schema") == 4 and payload.get("access") == "full")

    def allows_network(self) -> bool:
        return self.automatic()

    def grant_automatic(self, *, operator: str = "local-ui", full_access: bool = False) -> dict:
        """Local operator preference; full access requires an explicit opt-in.

        Distinct from legacy scoped grants: no session id, application lifetime or
        eight-hour expiry is used. Legacy schema 3 never silently becomes full.
        """
        payload = {"schema": 3, "mode": "automatic", "scope": "installation",
            "granted_at": time.time(), "expires_at": None, "operator": operator,
            "session_id": None, "applications": ["*"], "tools": ["*"],
            "actions": [], "max_risk": 3, "network": True}
        if full_access:
            payload.update(schema=4, access="full")
        self._save(payload)
        return payload

    def _save(self, payload):
        with self._exclusive():
            temporary = self.path.with_suffix(f".{secrets.token_hex(8)}.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(sign_record(self.directory, payload), stream, ensure_ascii=False)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def allows(self, session_id: str | None = None, application: str | None = None,
               tool: str | None = None, action: str | None = None) -> bool:
        payload = self.status()
        if payload is None:
            return False
        if session_id is None:
            return True  # Local UI status only; authorization always supplies all scopes.
        if payload.get("schema") in {3, 4}:
            return bool(session_id) and tool in self.automatic_tools()
        return (payload.get("schema") == 2 and payload.get("session_id") == session_id
                and application is not None and application.casefold() in payload.get("applications", [])
                and tool in payload.get("tools", [])
                and (not payload.get("actions") or action in payload["actions"]))

    def grant(self, mode: str = "session", *, operator: str = "local-ui", session_id: str,
              applications: list[str], tools: list[str] | None = None, actions: list[str] | None = None) -> dict:
        from windows_mcp.infrastructure.authorization import DESKTOP_TOOLS, TRUST_TOOLS

        if mode not in MODES:
            raise ValueError(f"Unknown trust mode: {mode!r}")
        if not isinstance(session_id, str) or not session_id.strip() or not applications:
            raise ValueError("Automatic authorization requires a client session and explicit applications")
        if any(not isinstance(app, str) or not app.strip() or app in {"*", "unresolved", "desktop:launch"} for app in applications):
            raise ValueError("Automatic authorization requires concrete application identities")
        if tools is None:
            tools = sorted(set().union(*(DESKTOP_TOOLS if not app.startswith(("native:", "provider:")) else
                {"NativeAPI", "FileSystem"} if app == "native:filesystem" else {"Browser"} if app == "provider:browser" else set()
                for app in applications)))
        if not tools or not set(tools) <= TRUST_TOOLS or (actions is not None and any(not isinstance(action, str) or not action for action in actions)):
            raise ValueError("Trust must name supported R2 tools and concrete actions")
        now = time.time()
        payload = {
            "schema": 2,
            "mode": mode,
            "granted_at": now,
            "expires_at": None if mode == "forever" else now + self.session_ttl,
            "operator": operator,
            "session_id": session_id,
            "applications": sorted({app.casefold() for app in applications}),
            "tools": sorted(set(tools)), "actions": sorted(set(actions or [])),
        }
        self._save(payload)
        return payload

    def grant_for_request(self, request: dict, mode: str = "session", *, operator: str = "approval-ui") -> dict:
        from windows_mcp.infrastructure.authorization import scope_applications

        scope = request.get("authorization_scope") or {}
        if request.get("risk") != 2 or not scope.get("application") or request.get("expires_at", 0) <= time.time():
            raise ValueError("Choose a scoped R2 request before enabling automatic authorization")
        return self.grant(mode, operator=operator, session_id=request["session_id"], applications=scope_applications(scope))

    def revoke(self) -> None:
        with self._exclusive():
            self.path.unlink(missing_ok=True)
