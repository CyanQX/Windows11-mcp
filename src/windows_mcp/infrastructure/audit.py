"""Bounded local audit log. Never stores tool arguments, text or exception messages."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = 5_000_000) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def record(self, *, session: str, tool: str, digest: str, status: str, audit_id: str | None = None) -> str:
        audit_id = audit_id or f"audit_{uuid.uuid4().hex}"
        record = {"audit_id": audit_id, "time": time.time(), "session_id": session,
                  "tool": tool, "action_hash": digest, "status": status}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                self.path.with_suffix(".2.jsonl").unlink(missing_ok=True)
                previous = self.path.with_suffix(".1.jsonl")
                if previous.exists():
                    previous.replace(self.path.with_suffix(".2.jsonl"))
                self.path.replace(previous)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return audit_id
