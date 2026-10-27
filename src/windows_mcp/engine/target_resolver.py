"""Resolve only server-issued targets in their original window/process lifetime."""

from __future__ import annotations

from windows_mcp.desktop import elements


def resolve(desktop, session, target: str, *, require_enabled: bool = True):
    session.check()
    with session.lock:
        record = session.targets.get(target)
    if not record or not target.startswith(f"uia:{session.session_id}:"):
        raise elements.StaleTarget("TARGET_STALE: target belongs to an old observation or another client")
    return resolve_record(desktop, session, record, require_enabled=require_enabled)


def resolve_record(desktop, session, record: dict, *, require_enabled: bool = False):
    session.check()
    window = elements.window_info(record["handle"])
    if window is None or window.pid != record["pid"]:
        raise elements.StaleTarget("TARGET_STALE: original window/process lifetime ended")
    element = elements.resolve_ref(desktop.element_registry, record["ref"])
    if element.window_handle != record["handle"] or element.pid != record["pid"]:
        raise elements.StaleTarget("TARGET_STALE: target identity changed; observe again")
    if record.get("runtime_id") is not None and element.runtime_id != record["runtime_id"]:
        raise elements.StaleTarget("TARGET_STALE: original control lifetime ended; observe again")
    if require_enabled and not element.enabled:
        raise elements.NotEnabledError("FAILED: target is disabled")
    return element
