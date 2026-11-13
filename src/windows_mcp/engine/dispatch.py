"""Per-action evidence recorded at the native/provider boundary, including partial input."""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class DispatchTrace:
    native_events: int = 0
    pointer_moves: int = 0
    button_downs: int = 0
    button_ups: int = 0
    keyboard_events: int = 0
    uia_calls: int = 0
    partial_native: bool = False
    motion: dict | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self._lock:
            return {key: getattr(self, key) for key in (
                "native_events", "pointer_moves", "button_downs", "button_ups",
                "keyboard_events", "uia_calls", "partial_native", "motion",
            )}


_trace: contextvars.ContextVar[DispatchTrace | None] = contextvars.ContextVar("wmcp_dispatch_trace", default=None)


@contextmanager
def trace_dispatch():
    trace = DispatchTrace()
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


def record_native(inputs, accepted: int) -> None:
    trace = _trace.get()
    if trace is None:
        return
    # SendInput inserts its array serially. Count only the accepted prefix, never intent.
    accepted = max(0, min(accepted, len(inputs)))
    with trace._lock:
        trace.native_events += accepted
        trace.partial_native |= accepted != len(inputs)
        for event in inputs[:accepted]:
            if event.type == 1:
                trace.keyboard_events += 1
            elif event.type == 0:
                flags = event.u.mi.dwFlags
                trace.pointer_moves += bool(flags & 0x0001)
                trace.button_downs += bool(flags & (0x0002 | 0x0008 | 0x0020))
                trace.button_ups += bool(flags & (0x0004 | 0x0010 | 0x0040))


def record_uia() -> None:
    """The provider is about to be called; its completion/effect is still unverified."""
    trace = _trace.get()
    if trace is not None:
        with trace._lock:
            trace.uia_calls += 1


def record_motion(evidence: dict) -> None:
    trace = _trace.get()
    if trace is not None:
        with trace._lock:
            trace.motion = dict(evidence)
