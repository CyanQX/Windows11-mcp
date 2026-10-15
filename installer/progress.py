"""Truthful, monotonic work progress; animation belongs exclusively to the view."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class TaskProgress:
    fraction: float = 0.0
    message: str = "准备开始"
    finished: bool = False
    failed: bool = False

    def advance(self, fraction: float, message: str):
        if self.finished or not math.isfinite(fraction):
            return
        # Only successful completion may reach 100. Retries/rollback never rewind.
        self.fraction = max(self.fraction, min(0.995, max(0.0, fraction)))
        self.message = message

    def complete(self, ok=True):
        self.finished, self.failed = True, not ok
        if ok:
            self.fraction = 1.0
        self.message = "操作完成" if ok else "操作未完成，请查看诊断"


def segment(callback, start, end):
    """Map completed work in a bounded subtask into its parent's fixed work budget."""
    return lambda fraction, message: callback(start + (end - start) * fraction, message)
