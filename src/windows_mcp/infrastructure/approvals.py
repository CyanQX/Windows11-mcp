"""Local operator approvals, bound to an exact call and consumed once.

No MCP tool can grant an approval. The local ``approve`` CLI is the trust boundary;
signed records live outside the tool-accessible workspace.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any


def action_hash(tool: str, arguments: dict[str, Any]) -> str:
    data = json.dumps([tool, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def preview(arguments: dict[str, Any]) -> dict[str, Any]:
    hidden = {"content", "text", "value", "command", "approval_token", "password", "token"}
    return {
        key: (f"<redacted; {len(str(value))} characters>" if key in hidden else str(value)[:300])
        for key, value in arguments.items()
    }


class ApprovalStore:
    def __init__(self, directory: Path, *, ttl: float = 300.0) -> None:
        self.directory = directory.resolve()
        self.ttl = ttl
        self._lock = threading.RLock()

    def _key(self) -> bytes:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "authority.key"
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

    def _path(self, request_id: str) -> Path:
        if len(request_id) != 32 or any(ch not in "0123456789abcdef" for ch in request_id):
            raise ValueError("Invalid approval request id")
        return self.directory / f"{request_id}.json"

    def _save(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        record = {"payload": payload, "signature": hmac.new(self._key(), body, "sha256").hexdigest()}
        path = self._path(payload["request_id"])
        temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
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
        with self._lock:
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
            self._save(payload)
            return payload

    def approve(self, request_id: str) -> dict:
        with self._lock:
            payload = self.read(request_id)
            payload["approved"] = True
            self._save(payload)
            return payload

    def consume(self, session_id: str, tool: str, arguments: dict, request_id: str | None = None) -> bool:
        digest = action_hash(tool, arguments)
        with self._lock:
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
            if path.suffix in {".json", ".used", ".tmp"} and time.time() - path.stat().st_mtime > self.ttl * 2:
                path.unlink(missing_ok=True)
        if len(list(self.directory.glob("*.json"))) >= 128:
            raise RuntimeError("Too many pending approvals; let existing requests expire")
