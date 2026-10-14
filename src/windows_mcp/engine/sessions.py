"""Server-owned client identities and bounded observations. No agent-selected owner ids."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
import weakref
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from windows_mcp.engine.contracts import Observation
from windows_mcp.infrastructure.audit import AuditLog
from windows_mcp.infrastructure.config import CONFIG_DIR
from windows_mcp.infrastructure.policy import PolicyGate


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex}")
    revision: int = 0
    cancelled: threading.Event = field(default_factory=threading.Event)
    observations: OrderedDict[str, Observation] = field(default_factory=OrderedDict)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    visuals: dict[str, Any] = field(default_factory=dict)
    desktop_state: Any = None
    legacy_registry: Any = None
    touched: float = field(default_factory=time.monotonic)
    active_calls: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise RuntimeError("CANCELLED: session cancelled; use Session(operation='resume')")

    def store(self, scope: str, observation: Observation, targets: dict[str, dict]) -> None:
        previous = self.observations.get(scope)
        if previous:
            for item in previous.elements:
                self.targets.pop(item.element_id, None)
        self.observations[scope] = observation
        self.observations.move_to_end(scope)
        self.targets.update(targets)
        while len(self.observations) > 8:
            _, removed = self.observations.popitem(last=False)
            for item in removed.elements:
                self.targets.pop(item.element_id, None)


_bound: contextvars.ContextVar[SessionState | None] = contextvars.ContextVar("wmcp_session", default=None)


class EngineRuntime:
    def __init__(self, policy: PolicyGate | None = None) -> None:
        self.policy = policy or PolicyGate()
        self.audit = AuditLog(CONFIG_DIR / "audit.jsonl")
        self.sessions: OrderedDict[str, SessionState] = OrderedDict()
        self._lock = threading.Lock()
        self._endpoint_owners = weakref.WeakKeyDictionary()
        self.normalizers: dict[str, Any] = {}
        self.browser = None
        self.watchdog = None

    def owner(self, context) -> str:
        """Use the trusted transport endpoint when SDK 2 recreates Connection per exchange.

        Context.session_id alone is not stable for the modern single-exchange stdio path.
        The outbound dispatcher belongs to the client duplex stream and cannot be chosen
        through tool arguments. Weak keys prevent object-id reuse from reviving an owner.
        """
        session = context.session
        connection = getattr(session, "_connection", None)
        if connection is not None:
            negotiated = getattr(connection, "session_id", None)
            if negotiated:
                return f"negotiated:{negotiated}"
            endpoint = getattr(connection, "outbound", None)
            endpoint = getattr(endpoint, "_outbound", endpoint)
            if endpoint is not None and type(endpoint).__name__ != "_NoChannelOutbound":
                try:
                    with self._lock:
                        if endpoint not in self._endpoint_owners:
                            self._endpoint_owners[endpoint] = f"endpoint:{uuid.uuid4().hex}"
                        return self._endpoint_owners[endpoint]
                except TypeError:
                    pass  # An SDK endpoint without weakref support falls back safely.
        return str(context.session_id)

    def session(self, owner: str = "local") -> SessionState:
        with self._lock:
            if owner not in self.sessions:
                if len(self.sessions) >= 64:
                    raise RuntimeError("Session capacity reached; reconnect after server cleanup")
                self.sessions[owner] = SessionState()
            state = self.sessions[owner]
            state.touched = time.monotonic()
            return state

    def current(self) -> SessionState:
        return _bound.get() or self.session()

    @contextmanager
    def bind(self, owner: str):
        token = _bound.set(self.session(owner))
        try:
            yield self.current()
        finally:
            _bound.reset(token)

    def normalize(self, tool: str, arguments: dict) -> dict:
        if tool not in self.normalizers:
            raise ValueError(f"Unknown or unavailable tool {tool}")
        return self.normalizers[tool](arguments)

    async def close(self) -> None:
        for state in self.sessions.values():
            state.cancelled.set()
        if self.browser:
            await self.browser.close_all()
        from windows_mcp.runtime import run_on_desktop

        for state in self.sessions.values():
            await run_on_desktop(self._release, state)
        self.sessions.clear()

    @staticmethod
    def _release(state: SessionState) -> None:
        state.legacy_registry = None
        state.desktop_state = None
        state.targets.clear()
        state.visuals.clear()
        state.observations.clear()

    async def sweep(self) -> None:
        from windows_mcp.runtime import run_on_desktop

        expired = [(owner, state) for owner, state in self.sessions.items()
                   if state.active_calls == 0 and time.monotonic() - state.touched > 900]
        for owner, state in expired:
            state.cancelled.set()
            if self.browser:
                await self.browser.close_session(state.session_id)
            await run_on_desktop(self._release, state)
            with self._lock:
                self.sessions.pop(owner, None)


_runtime: EngineRuntime | None = None


def get_runtime() -> EngineRuntime:
    global _runtime
    if _runtime is None:
        _runtime = EngineRuntime()
    return _runtime


def configure_runtime(policy: PolicyGate) -> EngineRuntime:
    global _runtime
    _runtime = EngineRuntime(policy)
    return _runtime
