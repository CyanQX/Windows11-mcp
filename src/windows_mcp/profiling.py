"""Opt-in timing of the Snapshot pipeline (``WINDOWS_MCP_PROFILE_SNAPSHOT=1``)."""

from __future__ import annotations

import os
from time import perf_counter


def snapshot_profile_enabled() -> bool:
    value = os.getenv("WINDOWS_MCP_PROFILE_SNAPSHOT", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class StageTimer:
    """Named lap timer; ``lap`` is a no-op unless profiling is enabled."""

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = snapshot_profile_enabled() if enabled is None else enabled
        self.started = perf_counter()
        self._lap_start = self.started
        self.stages: dict[str, float] = {}

    def lap(self, name: str) -> None:
        """Record the milliseconds since the previous lap under ``name``."""
        if self.enabled:
            now = perf_counter()
            self.stages[name] = (now - self._lap_start) * 1000
            self._lap_start = now

    def ms(self, name: str) -> float:
        return self.stages.get(name, 0.0)

    def total_ms(self) -> float:
        return (perf_counter() - self.started) * 1000
