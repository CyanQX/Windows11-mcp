"""Map screenshot candidates into virtual-desktop coordinates, including negative monitors."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenshotSpace:
    window_handle: int
    process_id: int
    rect: tuple[int, int, int, int]
    image_size: tuple[int, int]
    window_rect: tuple[int, int, int, int] | None = None
    process_started: float | None = None

    def point(self, x: float, y: float, *, live_handle: int, live_pid: int,
              live_rect: tuple[int, int, int, int]) -> tuple[int, int]:
        if (live_handle, live_pid, live_rect) != (self.window_handle, self.process_id, self.window_rect or self.rect):
            raise ValueError("TARGET_STALE: window identity/geometry changed since screenshot")
        width, height = self.image_size
        if width <= 0 or height <= 0 or not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("Invalid screenshot coordinates")
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError("Visual candidate lies outside the screenshot")
        left, top, right, bottom = self.rect
        return left + int(x * (right - left) / width), top + int(y * (bottom - top) / height)
