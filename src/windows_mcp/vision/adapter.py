"""Window-bound screenshot fallback with explicit geometry/lifetime checks."""

from __future__ import annotations

import io
import time
import uuid

import win32gui

from windows_mcp.desktop import elements, native_input
from windows_mcp.vision.grounding import ScreenshotSpace


def capture(desktop, session, window: str, reason: str) -> tuple[dict, bytes]:
    if reason not in {"no_semantic_target", "canvas", "provider_failure"}:
        raise ValueError("fallback_reason must be no_semantic_target, canvas or provider_failure")
    windows = elements.resolve_windows(window)
    if len(windows) != 1:
        raise elements.AmbiguousTarget("AMBIGUOUS_TARGET: visual capture requires exactly one window")
    info = windows[0]
    if elements.foreground_root() != info.handle or info.minimized:
        raise elements.FocusLostError("FOCUS_MISMATCH: switch the target window to the foreground before visual capture")
    rect = tuple(win32gui.GetWindowRect(info.handle))
    from windows_mcp.uia.core import Rect

    image = desktop.get_screenshot(Rect(*rect))
    if elements.foreground_root() != info.handle or tuple(win32gui.GetWindowRect(info.handle)) != rect:
        raise elements.StaleTarget("TARGET_STALE: window changed during screenshot capture")
    target = f"visual:{session.session_id}:{uuid.uuid4().hex}"
    with session.lock:
        session.visuals[target] = (ScreenshotSpace(info.handle, info.pid, rect, image.size), time.monotonic(), reason)
        while len(session.visuals) > 8:
            session.visuals.pop(next(iter(session.visuals)))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return {"target": target, "source": "visual", "window_id": str(info.handle), "process_id": info.pid,
            "image_size": list(image.size), "desktop_bounds": list(rect), "fallback_reason": reason,
            "expires_in_seconds": 60, "coordinate_space": "image_pixels"}, output.getvalue()


def click(session, target: str, coordinates: list[float]) -> str:
    with session.lock:
        record = session.visuals.get(target)
    if not record or time.monotonic() - record[1] > 60:
        raise elements.StaleTarget("TARGET_STALE: visual capture expired; capture again")
    space, _, reason = record
    info = elements.window_info(space.window_handle)
    if not info or elements.foreground_root() != space.window_handle:
        raise elements.FocusLostError("FOCUS_MISMATCH: visual target window lost foreground focus")
    try:
        x, y = space.point(*coordinates, live_handle=info.handle, live_pid=info.pid,
                          live_rect=tuple(win32gui.GetWindowRect(info.handle)))
    except ValueError as exc:
        raise elements.StaleTarget(str(exc)) from exc
    if elements.root_at(x, y) != space.window_handle:
        raise elements.OccludedError("Visual point is covered by another window; no click was dispatched")
    session.check()
    native_input.click(x, y)
    return reason
