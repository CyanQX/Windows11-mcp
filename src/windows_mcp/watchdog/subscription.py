"""Thread-safe, bounded multi-subscriber event hub. Queues contain data, never COM pointers."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Subscription:
    subscription_id: str
    owner_session: str
    window_id: int | None
    events: frozenset[str]
    expiry: float
    queue: deque = field(default_factory=lambda: deque(maxlen=64))
    overflow: bool = False
    signal: threading.Event = field(default_factory=threading.Event)


class SubscriptionHub:
    def __init__(self) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._lock = threading.RLock()
        self.revision = 0

    def subscribe(self, owner: str, window: int | None = None, events=("focus", "structure", "property"),
                  ttl: float = 30.0) -> Subscription:
        if not 0 < ttl <= 30 or not events or not set(events) <= {"focus", "structure", "property"}:
            raise ValueError("Watch subscriptions require recognized events and ttl in (0,30]")
        with self._lock:
            self.prune()
            if len(self._subscriptions) >= 64:
                raise RuntimeError("Watch subscription capacity reached")
            subscription = Subscription(uuid.uuid4().hex, owner, window, frozenset(events), time.monotonic() + ttl)
            self._subscriptions[subscription.subscription_id] = subscription
            return subscription

    def cancel(self, subscription_id: str, owner: str) -> None:
        with self._lock:
            subscription = self._subscriptions.get(subscription_id)
            if subscription and subscription.owner_session == owner:
                del self._subscriptions[subscription_id]
                subscription.signal.set()

    def publish(self, event: str, window: int | None, *, runtime_id: tuple | None = None,
                property_id: int | None = None) -> None:
        with self._lock:
            self.prune()
            self.revision += 1
            for subscription in self._subscriptions.values():
                if event not in subscription.events or (
                    subscription.window_id is not None and window != subscription.window_id
                ):
                    continue
                item = {"event": event, "window_id": window, "runtime_id": runtime_id,
                        "property_id": property_id, "revision": self.revision}
                # Coalesce duplicate bursts while preserving queue overflow diagnostics.
                if subscription.queue and all(subscription.queue[-1].get(k) == item.get(k)
                                              for k in ("event", "window_id", "runtime_id", "property_id")):
                    subscription.queue[-1] = item
                else:
                    if len(subscription.queue) == subscription.queue.maxlen:
                        subscription.overflow = True
                    subscription.queue.append(item)
                subscription.signal.set()

    def drain(self, subscription: Subscription) -> tuple[list[dict], bool]:
        with self._lock:
            items, overflow = list(subscription.queue), subscription.overflow
            subscription.queue.clear()
            subscription.overflow = False
            subscription.signal.clear()
            return items, overflow

    def prune(self) -> None:
        with self._lock:
            for key, subscription in list(self._subscriptions.items()):
                if subscription.expiry <= time.monotonic():
                    subscription.signal.set()
                    del self._subscriptions[key]

    @property
    def active(self) -> int:
        with self._lock:
            self.prune()
            return len(self._subscriptions)
