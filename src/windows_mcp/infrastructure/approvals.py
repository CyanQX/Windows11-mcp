"""Local operator approvals, bound to an exact call and consumed once.

No MCP tool can grant an approval. The local human GUI / ``approve`` CLI is the trust boundary;
signed records live outside the tool-accessible workspace.
"""

from __future__ import annotations

import hashlib
import base64
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from windows_mcp.infrastructure.local_storage import FileLock, private_directory


def action_hash(tool: str, arguments: dict[str, Any]) -> str:
    data = json.dumps([tool, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def preview(arguments: dict[str, Any]) -> dict[str, Any]:
    hidden = {"content", "text", "value", "command", "approval_token", "password", "token"}
    return {
        key: (f"<redacted; {len(str(value))} characters>" if key in hidden else str(value)[:300])
        for key, value in arguments.items()
    }


def authority_key(directory: Path) -> bytes:
    """Read or create the per-installation signing key shared by approvals and trust."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "authority.key"
    if not path.exists():
        key = secrets.token_bytes(32)
        temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
        temporary.write_bytes(key)
        try:
            # Publish a fully written key atomically without overwriting an existing authority.
            os.link(temporary, path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    key = path.read_bytes()
    if len(key) != 32:
        raise PermissionError("Invalid local approval authority key")
    return key


def sign_record(directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {"payload": payload, "signature": hmac.new(authority_key(directory), body, "sha256").hexdigest()}


def read_signed_record(directory: Path, path: Path) -> dict[str, Any] | None:
    """Return the verified payload, or None when the record is missing, malformed or forged."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        payload = record["payload"]
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        signature = hmac.new(authority_key(directory), body, "sha256").hexdigest()
        if not hmac.compare_digest(signature, record["signature"]):
            return None
        return payload
    except (OSError, ValueError, KeyError, TypeError, PermissionError):
        return None


class ApprovalStore:
    def __init__(self, directory: Path, *, ttl: float = 300.0) -> None:
        self.directory = directory.resolve()
        self.ttl = ttl
        self._lock = threading.RLock()

    @contextmanager
    def _exclusive(self):
        with self._lock:
            private_directory(self.directory)
            with FileLock(self.directory / ".operator.lock", timeout=2):
                yield

    def _key(self) -> bytes:
        return authority_key(self.directory)

    def _path(self, request_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid approval request id")
        return self.directory / f"{request_id}.json"

    def _save(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        record = {"payload": payload, "signature": hmac.new(self._key(), body, "sha256").hexdigest()}
        path = self._path(payload["request_id"])
        temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
        if not temporary.is_relative_to(self.directory):
            raise ValueError("Approval record is outside its private directory")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
        os.replace(temporary, path)

    def read(self, request_id: str) -> dict:
        record = json.loads(self._path(request_id).read_text(encoding="utf-8"))
        body = json.dumps(record["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8")
        signature = hmac.new(self._key(), body, "sha256").hexdigest()
        if not hmac.compare_digest(signature, record["signature"]):
            raise PermissionError("Approval signature mismatch")
        payload = record["payload"]
        if payload["request_id"] != request_id or payload["expires_at"] <= time.time():
            raise PermissionError("Approval expired or invalid")
        return payload

    def request(self, session_id: str, tool: str, arguments: dict, risk: int, description: str) -> dict:
        with self._exclusive():
            self.prune()
            payload = {
                "request_id": secrets.token_hex(16),
                "session_id": session_id,
                "task_id": session_id,
                "action_hash": action_hash(tool, arguments),
                "tool": tool,
                "scope": preview(arguments),
                "risk": risk,
                "description": description[:1000],
                "expires_at": time.time() + self.ttl,
                "approved": False,
            }
            if os.name == "nt":
                import win32crypt
                raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")
                payload["operator_details"] = base64.b64encode(win32crypt.CryptProtectData(raw, "Windows-MCP exact action", None, None, None, 1)).decode("ascii")
            self._save(payload)
            return payload

    def approve(self, request_id: str) -> dict:
        with self._exclusive():
            payload = self.read(request_id)
            if payload.get("rejected"):
                raise PermissionError("Approval was rejected")
            payload["approved"] = True
            self._save(payload)
            return payload

    def reject(self, request_id: str) -> None:
        with self._exclusive():
            self.read(request_id)
            os.rename(self._path(request_id), self._path(request_id).with_suffix(".rejected"))

    def pending(self) -> list[dict]:
        rows = []
        if not self.directory.exists():
            return rows
        for path in self.directory.glob("*.json"):
            try:
                rows.append(self.read(path.stem))
            except (OSError, ValueError, KeyError, PermissionError):
                continue
        return sorted(rows, key=lambda row: row["expires_at"])

    def operator_arguments(self, request_id: str) -> dict | None:
        payload = self.read(request_id)
        if not payload.get("operator_details") or os.name != "nt":
            return None  # Legacy requests remain reviewable via their signed scope/hash.
        import win32crypt
        raw = win32crypt.CryptUnprotectData(base64.b64decode(payload["operator_details"]), None, None, None, 1)[1]
        arguments = json.loads(raw)
        if action_hash(payload["tool"], arguments) != payload["action_hash"]:
            raise PermissionError("Operator details do not match the exact action")
        return arguments

    def consume(self, session_id: str, tool: str, arguments: dict, request_id: str | None = None) -> bool:
        digest = action_hash(tool, arguments)
        with self._exclusive():
            try:
                candidates = [self._path(request_id)] if request_id else list(self.directory.glob("*.json"))
            except ValueError:
                return False
            for path in candidates:
                try:
                    payload = self.read(path.stem)
                    if (
                        payload["approved"] is not True
                        or payload["session_id"] != session_id
                        or payload["tool"] != tool
                        or payload["action_hash"] != digest
                    ):
                        continue
                    # rename fails if the source was already consumed by another process.
                    used = path.with_suffix(".used")
                    os.rename(path, used)
                    return True
                except (OSError, ValueError, KeyError, PermissionError):
                    continue
        return False

    def prune(self) -> None:
        for path in self.directory.glob("*"):
            if path.suffix in {".json", ".used", ".tmp", ".rejected"} and time.time() - path.stat().st_mtime > self.ttl * 2:
                path.unlink(missing_ok=True)
        if len(list(self.directory.glob("*.json"))) >= 128:
            raise RuntimeError("Too many pending approvals; let existing requests expire")
