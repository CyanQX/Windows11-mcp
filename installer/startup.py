"""Small bounded startup evidence; never record commands, tokens or full argv."""
import json
import os
import time


class StartupTrace:
    def __init__(self, directory):
        self.path = directory / "startup.jsonl"
        self.started = time.perf_counter()

    def record(self, stage, *, status="ok", error=None, child_pid=None):
        row = {"time": time.time(), "pid": os.getpid(), "stage": stage, "status": status,
               "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 2)}
        if error is not None:
            row["error_code"] = getattr(error, "code", type(error).__name__)
        if child_pid is not None:
            row["child_pid"] = child_pid
        try:
            if self.path.exists() and self.path.stat().st_size > 1048576:
                self.path.replace(self.path.with_suffix(".previous.jsonl"))
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")
        except OSError:
            pass  # Diagnostics failure must not replace the real startup result.
