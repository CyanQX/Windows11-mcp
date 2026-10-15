"""Real structured events, bounded private logs and previewable redacted reports."""

from __future__ import annotations

import json
import os
import platform
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from installer.storage import private_directory


class Redactor:
    def __init__(self, home: Path):
        self.home = str(home)
        self.username = home.name

    def clean(self, value):
        if isinstance(value, dict):
            return {key: "<redacted>" if re.search(r"token|secret|password|cookie|authorization|api.?key|credential", str(key), re.I) else self.clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(item) for item in value]
        if isinstance(value, Path):
            value = str(value)
        if not isinstance(value, str):
            return value
        text = re.sub(re.escape(self.home), lambda _: "%USERPROFILE%", value, flags=re.I)
        text = re.sub(r"(?i)(?:[A-Z]:[\\/]Users[\\/])[^\\/\s\"']+", "%USERPROFILE%", text)
        if self.username:
            text = re.sub(r"\b" + re.escape(self.username) + r"\b", "<user>", text, flags=re.I)
        text = re.sub(r"(?i)Bearer\s+[^\s\"',}]+", "Bearer <redacted>", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "<redacted>", text)
        text = re.sub(r'''(?i)((?:token|password|secret|api[_-]?key|cookie)["']?\s*[=:]\s*)["']?[^\s,;"'}]+["']?''', r"\1<redacted>", text)
        text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1<redacted>@", text)
        return text[:4000]


class EventLog:
    def __init__(self, data: Path, home: Path, version: str, emit=None, *, retention_days: int = 30, max_bytes: int = 50 * 1024 * 1024):
        self.root = data / "logs"
        private_directory(self.root)
        self.redactor, self.version = Redactor(home), version
        self.callback = emit
        self.session_id = uuid.uuid4().hex
        self.retention_days, self.max_bytes = retention_days, max_bytes
        self.events = []
        self._lock = threading.Lock()
        self._part = 0
        self.prune()

    def prune(self):
        files = sorted(self.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        used = 0
        for path in files:
            used += path.stat().st_size
            if time.time() - path.stat().st_mtime > self.retention_days * 86400 or used > self.max_bytes:
                path.unlink(missing_ok=True)

    def emit(self, phase: str, message: str, *, level="INFO", target_id=None, transaction_id=None, error_code=None, **details) -> dict:
        row = self.redactor.clean({"time_iso": datetime.now().astimezone().isoformat(timespec="seconds"), "session_id": self.session_id, "transaction_id": transaction_id, "target_id": target_id, "phase": phase, "level": level, "message": message, "error_code": error_code, **details})
        with self._lock:
            path = self.root / f"{self.session_id}-{self._part}.jsonl"
            if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
                self._part += 1
                path = self.root / f"{self.session_id}-{self._part}.jsonl"
                self.prune()
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.events.append(row)
            self.events = self.events[-3000:]
        if self.callback:
            self.callback(row)
        return row

    def history(self, query: str = "") -> list[dict]:
        rows = []
        for path in sorted(self.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            for line in path.read_text(encoding="utf-8").splitlines()[-3000:]:
                try:
                    row = json.loads(line)
                    if not query or query.casefold() in json.dumps(row, ensure_ascii=False).casefold():
                        rows.append(self.redactor.clean(row))
                except ValueError:
                    continue
            if len(rows) >= 10000:
                break
        return rows[:10000]

    def report(self, states: dict, health: dict | None = None) -> dict:
        return self.redactor.clean({"schema": 1, "application_version": self.version, "platform": {"system": platform.system(), "release": platform.release(), "version": platform.version(), "python": platform.python_version()}, "session_id": self.session_id, "included_data": ["程序与系统版本", "脱敏检测结论", "连接检查结果", "最近事件"], "targets": {key: value.to_dict() for key, value in states.items()}, "health": health or {}, "events": self.events[-200:], "excludes": ["完整客户端配置", "凭证", "浏览器数据", "私人文件", "机器唯一识别码"], "environment": {"native_windows": os.name == "nt"}})
