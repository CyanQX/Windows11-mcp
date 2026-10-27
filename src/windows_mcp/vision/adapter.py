"""HWND capture, optional local OCR and geometry-bound visual candidates."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import hashlib
import io
import subprocess
import time
import uuid
from contextlib import contextmanager

import psutil
import win32gui
from PIL import Image, ImageChops, ImageStat

from windows_mcp.desktop import elements, native_input
from windows_mcp.runtime import run_on_desktop
from windows_mcp.vision import native_bridge
from windows_mcp.vision.grounding import ScreenshotSpace


@contextmanager
def physical_coordinates():
    user32 = ctypes.windll.user32
    previous = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        yield
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def window_rects(hwnd: int) -> tuple[tuple, tuple]:
    with physical_coordinates():
        rect = tuple(win32gui.GetWindowRect(hwnd))
        visible = wt.RECT()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 9, ctypes.byref(visible), ctypes.sizeof(visible))
        frame = (visible.left, visible.top, visible.right, visible.bottom) if result == 0 else rect
    return rect, frame


def _prepare(window: str):
    windows = elements.resolve_windows(window)
    if len(windows) != 1:
        raise elements.AmbiguousTarget("AMBIGUOUS_TARGET: visual capture requires exactly one window")
    info = windows[0]
    if info.minimized:
        raise elements.FocusLostError("Minimized windows cannot supply a current window capture")
    rect, frame = window_rects(info.handle)
    return info, rect, frame, psutil.Process(info.pid).create_time()


def _recheck(info, rect, frame, started):
    current = elements.window_info(info.handle)
    if (current is None or current.pid != info.pid or current.minimized
            or psutil.Process(current.pid).create_time() != started or window_rects(info.handle) != (rect, frame)):
        raise elements.StaleTarget("TARGET_STALE: window identity/geometry changed during capture")


def _desktop_image(desktop, info, rect):
    if elements.foreground_root() != info.handle:
        raise elements.FocusLostError("Desktop-crop fallback requires the target in foreground; use a window capture backend")
    from windows_mcp.uia.core import Rect

    with physical_coordinates():
        return desktop.get_screenshot(Rect(*rect)).convert("RGB")


async def capture_async(desktop, session, window: str, reason: str, *, backend: str = "auto",
                        region: list[int] | None = None, max_width: int = 1600,
                        previous_target: str | None = None, only_if_changed: bool = False,
                        change_threshold: float = 0.0, ocr: bool = False,
                        ocr_backend: str = "auto", ocr_language: str | None = None) -> tuple[dict, bytes | None]:
    if reason not in {"no_semantic_target", "canvas", "provider_failure"}:
        raise ValueError("Unknown fallback_reason")
    if backend not in {"auto", "wgc", "printwindow", "desktop"} or ocr_backend not in {"auto", "windows_ocr", "tesseract"}:
        raise ValueError("Unknown capture/OCR backend")
    if not 64 <= max_width <= 4096 or not 0 <= change_threshold <= 1:
        raise ValueError("max_width must be 64..4096; change_threshold must be 0..1")
    if ocr_language is not None and (not ocr_language or len(ocr_language) > 50 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-' for c in ocr_language)):
        raise ValueError("Invalid OCR language identifier")
    if only_if_changed and previous_target is None:
        raise ValueError("only_if_changed requires previous_target")
    generation = session.generation
    session.check(generation)
    info, rect, frame, started = await run_on_desktop(_prepare, window)
    diagnostics = []
    image, used, capture_rect = None, None, None
    for candidate in (["wgc", "printwindow", "desktop"] if backend == "auto" else [backend]):
        session.check(generation)
        try:
            if candidate == "desktop":
                image = await run_on_desktop(_desktop_image, desktop, info, rect)
                capture_rect = rect
                diagnostics.append("Desktop crop may include covering windows; click hit-testing remains required")
            else:
                image = await asyncio.to_thread(native_bridge.capture, info.handle, candidate)
                bounds = frame if candidate == "wgc" else rect
                if image.size != (bounds[2] - bounds[0], bounds[3] - bounds[1]):
                    if candidate == "wgc" and image.size == (rect[2] - rect[0], rect[3] - rect[1]):
                        bounds = rect
                    else:
                        raise ValueError("Window frame geometry did not match physical bounds")
                capture_rect = bounds
            used = candidate
            break
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            image = None
            if backend != "auto":
                raise
            diagnostics.append(f"{candidate} unavailable/failed ({type(exc).__name__}); tried the next backend")
    if image is None:
        raise RuntimeError("No window capture backend produced an image")
    await run_on_desktop(_recheck, info, rect, frame, started)
    if region is not None:
        if (len(region) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
                or not 0 <= region[0] < region[2] <= image.width or not 0 <= region[1] < region[3] <= image.height):
            raise ValueError("region must be [left,top,right,bottom] within the unscaled captured image")
        image = image.crop(tuple(region))
        capture_rect = (capture_rect[0] + region[0], capture_rect[1] + region[1],
                        capture_rect[0] + region[2], capture_rect[1] + region[3])
    if image.width > max_width:
        image = image.resize((max_width, max(1, round(image.height * max_width / image.width))), Image.Resampling.LANCZOS)
    digest = hashlib.sha256(image.tobytes() + str(image.size).encode()).hexdigest()
    thumb = image.resize((64, 64)).convert("RGB")
    changed, difference = True, None
    if previous_target:
        with session.lock:
            previous = session.visuals.get(previous_target)
        if previous is None or len(previous) < 5:
            raise elements.StaleTarget("Previous visual capture is unavailable in this session")
        space = previous[0]
        if (space.window_handle != info.handle or space.process_id != info.pid
                or (space.process_started is not None and space.process_started != started)):
            raise elements.StaleTarget("Previous capture belongs to another window")
        if space.rect == capture_rect and space.image_size == image.size:
            difference = sum(ImageStat.Stat(ImageChops.difference(thumb, previous[4])).mean) / (3 * 255)
            changed = digest != previous[3] and (change_threshold == 0 or difference > change_threshold)
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    result = {"source": "visual", "window_id": str(info.handle), "process_id": info.pid,
        "image_size": list(image.size), "desktop_bounds": list(capture_rect), "window_bounds": list(rect),
        "fallback_reason": reason, "capture_backend": used, "background_observation": await run_on_desktop(elements.foreground_root) != info.handle,
        "expires_in_seconds": 60, "coordinate_space": "image_pixels", "diagnostics": diagnostics,
        "changed": changed, "difference_ratio": difference, "image_sha256": digest,
        "image_included": not only_if_changed or changed}
    if ocr:
        try:
            result["ocr"] = await asyncio.to_thread(native_bridge.recognize, data, backend=ocr_backend, language=ocr_language)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result["ocr"] = {"available": False, "reason": type(exc).__name__, "words": []}
    await run_on_desktop(_recheck, info, rect, frame, started)
    session.check(generation)
    target = f"visual:{session.session_id}:{uuid.uuid4().hex}"
    with session.lock:
        session.check(generation)
        session.visuals[target] = (ScreenshotSpace(info.handle, info.pid, capture_rect, image.size,
            window_rect=rect, process_started=started), time.monotonic(), reason, digest, thumb)
        while len(session.visuals) > 8:
            session.visuals.pop(next(iter(session.visuals)))
    result["target"] = target
    return result, data if result["image_included"] else None


def capture(desktop, session, window: str, reason: str, **options):
    """Synchronous internal compatibility wrapper; tools use capture_async."""
    return asyncio.run(capture_async(desktop, session, window, reason, **options))


def click(session, target: str, coordinates: list[float]) -> str:
    with session.lock:
        record = session.visuals.get(target)
    if not record or time.monotonic() - record[1] > 60:
        raise elements.StaleTarget("TARGET_STALE: visual capture expired; capture again")
    space, _, reason = record[:3]
    info = elements.window_info(space.window_handle)
    if info and space.process_started is not None and psutil.Process(info.pid).create_time() != space.process_started:
        raise elements.StaleTarget("TARGET_STALE: visual target process lifetime changed")
    if not info or elements.foreground_root() != space.window_handle:
        raise elements.FocusLostError("FOCUS_MISMATCH: visual target window lost foreground focus")
    with physical_coordinates():
        try:
            x, y = space.point(*coordinates, live_handle=info.handle, live_pid=info.pid,
                              live_rect=window_rects(info.handle)[0])
        except ValueError as exc:
            raise elements.StaleTarget(str(exc)) from exc
        if elements.root_at(x, y) != space.window_handle:
            raise elements.OccludedError("Visual point is covered by another window; no click was dispatched")
        session.check()
        native_input.click(x, y)
    return reason
