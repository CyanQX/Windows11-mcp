"""Event-triggered reads with bounded polling fallback; event arrival is never evidence."""

from __future__ import annotations

import asyncio
import time

from windows_mcp.desktop import elements
from windows_mcp.engine.contracts import normalize_expected
from windows_mcp.engine.observe_router import observe_desktop
from windows_mcp.engine.target_resolver import resolve_record
from windows_mcp.engine.verifier import verify
from windows_mcp.runtime import run_on_desktop
from windows_mcp.watchdog.subscription import SubscriptionHub

FALLBACK_SECONDS = 1.5


def _state(observation: dict) -> dict:
    return {item["element_id"].rsplit(":", 1)[-1]: {k: v for k, v in item.items() if k != "element_id"}
            for item in observation["elements"]}


async def watch(desktop, runtime, session, *, window=None, expected=None, events=None,
                timeout_ms: int = 10000) -> dict:
    if not 100 <= timeout_ms <= 30000:
        raise ValueError("timeout_ms must be 100..30000")
    expected = normalize_expected(expected)
    generation, deadline = session.generation, time.monotonic() + timeout_ms / 1000
    hub = runtime.watchdog.subscriptions if runtime.watchdog else SubscriptionHub()
    subscription = None
    record = None

    async def query(func, *args, **kwargs):
        def checked():
            session.check(generation)
            if time.monotonic() >= deadline:
                raise TimeoutError("Watch query expired in the desktop queue")
            result = func(*args, **kwargs)
            session.check(generation)
            return result

        return await asyncio.wait_for(run_on_desktop(checked), max(0.001, deadline - time.monotonic()))

    def scope():
        nonlocal record
        if expected and expected["kind"] == "element_state":
            record = session.targets.get(expected["target"])
            if not record:
                raise elements.StaleTarget("Observe the watched control first")
            resolve_record(desktop, session, record)
            if window is None:
                return record["handle"]
        if window is None and expected:
            return None
        windows = elements.resolve_windows(window)
        if len(windows) != 1:
            raise elements.StaleTarget("Watch requires one live window")
        if record and windows[0].handle != record["handle"]:
            raise elements.StaleTarget("Watched control belongs to another window")
        return windows[0].handle

    def evidence():
        current = resolve_record(desktop, session, record) if record else None
        return verify(desktop, runtime, session, expected, resolved_element=current)

    all_events, overflow_seen = [], False
    baseline, initial, handle = {}, None, None
    try:
        handle = await query(scope)
        # Subscribe before the first read to cover changes during initial sampling.
        subscription = hub.subscribe(session.session_id, handle,
            events if events is not None else ["focus", "structure", "property"],
            min(30.0, max(0.001, deadline - time.monotonic())))
        if expected is None:
            initial = await query(observe_desktop, desktop, session, window=str(handle), limit=100)
            baseline = _state(initial)
        next_poll = time.monotonic() + FALLBACK_SECONDS
        first_check = expected is not None
        while time.monotonic() < deadline:
            session.check(generation)
            items, overflow = hub.drain(subscription)
            all_events.extend(items)
            overflow_seen |= overflow or len(all_events) > 64
            all_events = all_events[-64:]
            polling = time.monotonic() >= next_poll
            if first_check or polling or items or overflow:
                first_check = False
                if polling:
                    next_poll = time.monotonic() + FALLBACK_SECONDS
                if expected:
                    result = await query(evidence)
                    if result.passed is True:
                        return {"status": "VERIFIED", "subscription_id": subscription.subscription_id,
                                "revision": session.revision, "events": all_events, "overflow": overflow_seen,
                                "verification": result.model_dump()}
                elif initial is not None:
                    targets = [item["element_id"] for item in initial["elements"]]
                    with session.lock:
                        known_ids = {tuple(session.targets.get(target, {}).get("runtime_id") or ()) for target in targets}
                    local = (bool(items) and not polling and not overflow
                             and all(item["event"] != "structure" and item.get("runtime_id")
                                     and tuple(item["runtime_id"]) in known_ids for item in items))
                    if local and targets:
                        changed_ids = {tuple(item["runtime_id"]) for item in items}
                        with session.lock:
                            targets = [target for target in targets if tuple(session.targets.get(target, {}).get("runtime_id") or ()) in changed_ids]
                        try:
                            current = await query(observe_desktop, desktop, session, targets=targets, mode="refresh")
                        except elements.StaleTarget:
                            local = False
                            current = await query(observe_desktop, desktop, session, window=str(handle), limit=100)
                    else:
                        current = await query(observe_desktop, desktop, session, window=str(handle), limit=100)
                    current_state = baseline | _state(current) if local else _state(current)
                    if (current_state != baseline or any(current["context"].get(key) != initial["context"].get(key) for key in ("title", "foreground"))):
                        return {"status": "VERIFIED", "subscription_id": subscription.subscription_id,
                            "revision": current["revision"], "events": all_events, "overflow": overflow_seen,
                            "verification": {"passed": True, "evidence_type": "semantic_state_change",
                                             "detail": "A live state read differed from the baseline"},
                            "observation": current}
                    if local:
                        updated = {item["element_id"].rsplit(":", 1)[-1]: item for item in current["elements"]}
                        initial["elements"] = [updated.get(item["element_id"].rsplit(":", 1)[-1], item) for item in initial["elements"]]
                    else:
                        initial = current
            remaining = min(deadline - time.monotonic(), next_poll - time.monotonic(), 0.1)
            if remaining > 0:
                await asyncio.to_thread(subscription.signal.wait, remaining)
        return {"status": "TIMEOUT", "subscription_id": subscription.subscription_id,
                "revision": session.revision, "events": all_events, "overflow": overflow_seen,
                "diagnostics": ["Condition did not match; event silence is not evidence of success"]}
    except TimeoutError:
        return {"status": "TIMEOUT", "events": all_events, "overflow": overflow_seen,
                "diagnostics": ["Watch read exceeded its deadline; an active provider read cannot be interrupted"]}
    except elements.StaleTarget as exc:
        return {"status": "TARGET_STALE", "diagnostics": [str(exc)]}
    except RuntimeError:
        if session.cancelled.is_set() or session.generation != generation:
            return {"status": "CANCELLED", "subscription_id": subscription.subscription_id if subscription else None}
        raise
    finally:
        if subscription:
            hub.cancel(subscription.subscription_id, session.session_id)
