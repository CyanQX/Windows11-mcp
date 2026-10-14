"""Bounded semantic observations and revision deltas over the existing UIA layer."""

from __future__ import annotations

import uuid
import json

from windows_mcp.desktop import elements
from windows_mcp.engine.contracts import Bounds, Observation, SemanticElement
from windows_mcp.engine.sessions import SessionState


def observe_desktop(desktop, session: SessionState, *, window: str | int | None = None,
                    limit: int = 100, max_depth: int = 8, include_offscreen: bool = False,
                    since_revision: int | None = None) -> dict:
    if not 1 <= limit <= 300 or not 1 <= max_depth <= 20:
        raise ValueError("limit must be 1..300 and max_depth must be 1..20")
    session.check()
    windows = elements.resolve_windows(window)
    if len(windows) != 1:
        if not windows:
            raise elements.StaleTarget("TARGET_STALE: no window matches; observe open windows again")
        raise elements.AmbiguousTarget("AMBIGUOUS_TARGET: multiple windows match; supply a window handle")
    info = windows[0]
    scope = f"uia:{info.handle}:{info.pid}"
    found, truncated, diagnostics = elements.walk_bounded(
        info, limit=limit, max_depth=max_depth, include_offscreen=include_offscreen
    )
    with session.lock:
        previous = session.observations.get(scope)
        # Identity includes process/window scope; raw pointers stay on the desktop worker.
        signatures = json.dumps([info.title, elements.foreground_root() == info.handle,
            [(e.runtime_id, e.name, e.rect, e.enabled, e.offscreen, e.focused, e.automation_id, sorted(e.capabilities), e.state) for e in found]], sort_keys=True)
        old_signature = getattr(previous, "_signature", None) if previous else None
        if signatures == old_signature:
            revision = previous.revision
        else:
            session.revision += 1
            revision = session.revision
        nodes, targets = [], {}
        for element in found:
            ref = desktop.element_registry.register(element)
            target = f"uia:{session.session_id}:{revision}:{ref}"
            state = {key: (value[:1000] if isinstance(value, str) else value)
                     for key, value in element.state.items() if not element.password or key != "value"}
            state["focused"] = element.focused
            nodes.append(SemanticElement(
                element_id=target, role=element.control_type, name=element.name[:300],
                automation_id=element.automation_id[:200], enabled=element.enabled,
                visible=not element.offscreen, bounds=Bounds(left=element.rect[0], top=element.rect[1],
                                                          right=element.rect[2], bottom=element.rect[3]),
                state=state, actions=sorted(element.capabilities),
            ))
            targets[target] = {"scope": scope, "ref": ref, "handle": info.handle,
                               "pid": info.pid, "runtime_id": element.runtime_id}
        observation = Observation(
            observation_id=f"obs_{uuid.uuid4().hex}", session_id=session.session_id,
            revision=revision, context={"source": "uia", "window_id": str(info.handle),
                                       "process_id": info.pid, "title": info.title[:300],
                                       "foreground": elements.foreground_root() == info.handle},
            elements=nodes, truncated=truncated, diagnostics=diagnostics,
        )
        observation._signature = signatures
        result = observation.model_dump()
        if since_revision is not None:
            if previous and since_revision == previous.revision:
                prior = {item.element_id.rsplit(":", 1)[-1]: item for item in previous.elements}
                current = {item.element_id.rsplit(":", 1)[-1]: item for item in nodes}
                changed = []
                for ref, item in current.items():
                    old = prior.get(ref)
                    if old is None or old.model_dump(exclude={"element_id"}) != item.model_dump(exclude={"element_id"}):
                        changed.append(item.model_dump())
                result["elements"] = changed
                result["removed"] = [prior[ref].element_id for ref in prior.keys() - current.keys()]
                result["context"]["delta"] = True
                result["context"]["base_revision"] = since_revision
                # A state change invalidates the previous revision, even for unchanged controls.
                result["context"]["refresh_targets"] = revision != since_revision
            else:
                result["diagnostics"].append("Requested revision is unavailable; returning a full observation")
        session.store(scope, observation, targets)
    return result
