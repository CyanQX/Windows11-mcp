"""Local feedback-controlled pointer gestures; no screenshots or model round trips."""

from __future__ import annotations

import contextvars
import ctypes
import ctypes.wintypes as wt
import math
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field

from windows_mcp.desktop.pointer_monitor import monitor
from windows_mcp.infrastructure.config import MotionSettings


_settings = contextvars.ContextVar("wmcp_pointer_settings", default=None)
_gesture = contextvars.ContextVar("wmcp_pointer_gesture", default=None)


@contextmanager
def bind_motion(settings):
    token = _settings.set(settings)
    try:
        yield
    finally:
        _settings.reset(token)


def enabled():
    settings = _settings.get()
    return settings is not None and settings.style == "natural"


def travel_seconds(distance: float, width: float = 24) -> float:
    """A bounded distance/target-size heuristic, tuned by measurement rather than noise."""
    return min(0.8, max(0.12, 0.09 + 0.08 * math.log2(1 + distance / max(1, width))))


def ease(t):
    return t * t * t * (10 + t * (-15 + 6 * t))


@dataclass
class _Gesture:
    session: object
    settings: MotionSettings
    token: object = None
    started: float = field(default_factory=lambda: time.monotonic())
    deadline: float = 0
    samples: int = 0
    replans: int = 0
    max_error: float = 0
    reason: str | None = None


def check_handoff():
    job = _gesture.get()
    if job is None:
        return
    reason = None
    if job.token is not None:
        if job.token.interrupted.is_set():
            reason = job.token.reason or "external_input"
        elif not monitor.healthy():
            reason = "pointer_monitor_stopped"
    if reason:
        job.reason = reason
        if job.session is not None:
            job.session.pointer_interruption = reason
            if not job.session.cancelled.is_set():
                job.session.cancel()
        raise RuntimeError(f"USER_INTERRUPTED: {reason}; inspect the result and explicitly resume the session")
    if time.monotonic() > job.deadline:
        job.reason = "motion_deadline"
        raise RuntimeError("POINTER_TIMEOUT: local gesture time budget expired; observe before retrying")


@contextmanager
def gesture(target=None, *, budget=3.0):
    if _gesture.get() is not None:
        yield _gesture.get()
        return
    from windows_mcp.engine.sessions import _bound
    from windows_mcp.engine.dispatch import record_motion

    settings = _settings.get() or MotionSettings()
    job = _Gesture(_bound.get(), settings)
    job.deadline = job.started + budget
    if settings.human_takeover:
        try:
            job.token = monitor.arm(getattr(target, "window_handle", 0))
        except RuntimeError as exc:
            if str(exc).startswith("USER_INTERRUPTED") and job.session is not None:
                job.session.pointer_interruption = "mouse_button_already_held"
                job.session.cancel()
            raise
    marker = _gesture.set(job)
    try:
        with target.tracking() if hasattr(target, "tracking") else nullcontext():
            yield job
    except BaseException as exc:
        if job.reason is None:
            job.reason = getattr(exc, "code", type(exc).__name__)
        raise
    finally:
        evidence = {"style": "natural", "elapsed_ms": round((time.monotonic() - job.started) * 1000, 2),
                    "samples": job.samples, "replans": job.replans,
                    "max_feedback_error_px": round(job.max_error, 2), "stop_reason": job.reason,
                    "human_takeover": settings.human_takeover,
                    "window_events": bool(job.token is not None and monitor.window_events)}
        record_motion(evidence)
        if job.session is not None:
            job.session.last_pointer_motion = evidence
        if job.token is not None:
            monitor.disarm(job.token)
        _gesture.reset(marker)


def _check_point(point, screen):
    from windows_mcp.desktop import native_input as ni

    x, y = point
    if not (screen.left <= x < screen.left + screen.width and screen.top <= y < screen.top + screen.height):
        raise ni.InputBlockedError("INPUT_TARGET_UNREACHABLE: pointer target is outside the virtual desktop")
    clip = wt.RECT()
    if not ni._user32.GetClipCursor(ctypes.byref(clip)):
        raise ni.InputBlockedError("INPUT_CURSOR_UNAVAILABLE: cursor restriction could not be queried")
    if not (clip.left <= x < clip.right and clip.top <= y < clip.bottom):
        raise ni.InputBlockedError("INPUT_TARGET_UNREACHABLE: target is outside the current ClipCursor restriction")


def _move_event(point, screen):
    from windows_mcp.desktop import native_input as ni

    # Aim at the center of the physical pixel in Windows' 16-bit absolute coordinate bins.
    x = max(0, min(65535, int((point[0] - screen.left + 0.5) * 65536 / screen.width)))
    y = max(0, min(65535, int((point[1] - screen.top + 0.5) * 65536 / screen.height)))
    return ni._mouse(ni.MOUSEEVENTF_MOVE | ni.MOUSEEVENTF_ABSOLUTE | ni.MOUSEEVENTF_VIRTUALDESK, x, y)


def move(x, y, *, target=None, duration=None):
    from windows_mcp.desktop import native_input as ni

    if _gesture.get() is None:
        with gesture(target, budget=max(3.0, (duration or 0) + 1.5)):
            return move(x, y, target=target, duration=duration)
    job = _gesture.get()
    screen = ni.virtual_screen()
    anchor = (int(x), int(y))

    def goal(force=False):
        if hasattr(target, "motion_target"):
            dirty = bool(job.token is not None and job.token.dirty.is_set())
            if dirty:
                job.token.dirty.clear()
            return target.motion_target(*anchor, force=force or dirty)
        return anchor, 24

    destination, width = goal(True)
    _check_point(destination, screen)
    origin = ni.cursor_position()
    if origin == destination:
        return destination
    segment = time.monotonic()
    travel = duration if duration is not None else travel_seconds(math.dist(origin, destination), width)
    previous, settling_since = origin, None
    while True:
        ni._check_active()
        updated, width = goal()
        current = ni.cursor_position()
        error = math.dist(current, previous)
        job.max_error = max(job.max_error, error)
        now = time.monotonic()
        # SendInput acceptance precedes cursor delivery. Do not mistake normal queue lag
        # during travel for a target change; correct residual error after a bounded settle.
        needs_correction = settling_since is not None and now - settling_since >= 0.1 and current != destination
        if needs_correction and job.token is not None and job.token.last_own_point == destination:
            job.token.reason = "external_cursor_reposition"
            job.token.interrupted.set()
            check_handoff()
        if updated != destination or needs_correction:
            if job.replans >= 3:
                raise ni.InputBlockedError(
                    f"TARGET_MOVING: pointer target/cursor did not stabilize within three replans "
                    f"(observed={current}, previous={previous}, target={updated}, "
                    f"delivered={getattr(job.token, 'last_own_point', None)}, screen={screen})")
            job.replans += 1
            destination, origin = updated, current
            _check_point(destination, screen)
            segment = time.monotonic()
            travel = travel_seconds(math.dist(origin, destination), width)
            settling_since = None
        t = 1.0 if travel == 0 else min(1.0, (time.monotonic() - segment) / travel)
        amount = ease(t)
        point = (round(origin[0] + (destination[0] - origin[0]) * amount),
                 round(origin[1] + (destination[1] - origin[1]) * amount))
        ni._send([_move_event(point, screen)])
        job.samples += 1
        previous = point
        ni._wait(1 / 90)
        if t >= 1:
            if ni.cursor_position() == destination:
                final, _ = goal(True)
                if final == destination:
                    return destination
            if settling_since is None:
                settling_since = time.monotonic()
            # The next feedback sample either corrects drift or follows the new target.


def click(x, y, *, button="left", count=1, target=None):
    from windows_mcp.desktop import native_input as ni

    settings = _settings.get() or MotionSettings()
    with gesture(target, budget=3 + settings.hover_ms / 1000) as job:
        point = move(x, y, target=target)
        dwell = settings.hover_ms / 1000 if count == 0 else 0.04
        until = time.monotonic() + dwell
        while True:
            remaining = until - time.monotonic()
            if remaining > 0:
                ni._wait(min(0.02, remaining))
            changed = False
            if hasattr(target, "motion_target"):
                dirty = bool(job.token is not None and job.token.dirty.is_set())
                if dirty:
                    job.token.dirty.clear()
                updated, _ = target.motion_target(x, y, force=dirty or remaining <= 0)
                changed = updated != point
                if changed:
                    if job.replans >= 3:
                        raise ni.InputBlockedError("TARGET_MOVING: target did not settle within three replans")
                    job.replans += 1
                    point = move(x, y, target=target)
                    until = time.monotonic() + dwell
            if not changed and remaining <= 0:
                break
        if target is not None:
            target(*point)
        if count == 0:
            return point
        gap = min(0.06, ni._user32.GetDoubleClickTime() / 5000)
        for index in range(count):
            ni._check_active()
            if target is not None:
                target(*point)
            if ni.cursor_position() != point:
                raise ni.InputBlockedError("INPUT_TARGET_UNREACHABLE: cursor left the target before pressing")
            ni.press(button)
            try:
                ni._wait(0.05)
            finally:
                ni.release(button)
            if index + 1 < count:
                ni._wait(gap)
        return point


def drag(start, end, *, button="left", duration=None, before_press=None, before_release=None):
    from windows_mcp.desktop import native_input as ni

    with gesture(before_release, budget=max(3.0, (duration or 0) + 2)):
        point = move(*start)
        if before_press is not None:
            before_press(*point)
        if ni.cursor_position() != point:
            raise ni.InputBlockedError("INPUT_TARGET_UNREACHABLE: drag start changed before pressing")
        ni.press(button)
        try:
            ni._wait(0.05)
            point = move(*end, target=before_release, duration=duration)
            ni._wait(0.05)
            if before_release is not None:
                before_release(*point)
        finally:
            ni.release(button)
        return point
