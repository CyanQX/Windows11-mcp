"""Track UIA calls that may outlive their caller. A timeout is not cancellation."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


class ActionInProgress(RuntimeError):
    code = "ACTION_IN_PROGRESS"

    def __init__(self, action_id: str) -> None:
        self.action_id = action_id
        super().__init__(f"{self.code}: {action_id}; read Session status and verify the result. "
                         "A timed-out action may still execute. Acknowledge it only after it finishes; never replay it blindly.")


_executing: contextvars.ContextVar[str | None] = contextvars.ContextVar("wmcp_pending_action", default=None)


@dataclass
class PendingAction:
    action_id: str
    session_id: str
    started: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    timed_out: bool = False
    value: Any = None
    error: BaseException | None = None


class PendingActions:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._action: PendingAction | None = None

    def check(self) -> None:
        with self._lock:
            if self._action and _executing.get() != self._action.action_id:
                raise ActionInProgress(self._action.action_id)

    def begin(self, session_id: str) -> PendingAction:
        with self._lock:
            if self._action is not None:
                raise ActionInProgress(self._action.action_id)
            action = PendingAction(f"action_{uuid.uuid4().hex}", session_id)
            self._action = action
            return action

    @contextmanager
    def executing(self, action: PendingAction):
        token = _executing.set(action.action_id)
        try:
            yield
        finally:
            _executing.reset(token)

    def completed(self, action: PendingAction, *, value=None, error: BaseException | None = None) -> None:
        with self._lock:
            action.value, action.error = value, error
            action.done.set()

    def finish_wait(self, action: PendingAction, finished: bool) -> None:
        with self._lock:
            if not finished:
                action.timed_out = True
                raise ActionInProgress(action.action_id)
            if self._action is action:
                self._action = None
        if action.error is not None:
            raise action.error

    def status(self) -> dict | None:
        with self._lock:
            action = self._action
            if action is None:
                return None
            return {"action_id": action.action_id, "session_id": action.session_id,
                    "state": "COMPLETED" if action.done.is_set() else "RUNNING",
                    "timed_out": action.timed_out, "elapsed_seconds": round(time.monotonic() - action.started, 3),
                    "error_type": type(action.error).__name__ if action.error is not None else None,
                    "result_requires_verification": True}

    def acknowledge(self, session_id: str, action_id: str | None) -> None:
        with self._lock:
            action = self._action
            if action is None or action.action_id != action_id or action.session_id != session_id:
                raise ValueError("No pending action with this id belongs to this session")
            if not action.done.is_set():
                raise ActionInProgress(action.action_id)
            self._action = None

    def owns(self, session_id: str) -> bool:
        with self._lock:
            return self._action is not None and self._action.session_id == session_id
