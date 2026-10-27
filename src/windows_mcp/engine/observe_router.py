"""Bounded window/subtree observations and explicit incremental target refreshes."""

from __future__ import annotations

import hashlib
import json
import uuid

from windows_mcp.desktop import elements
from windows_mcp.engine.contracts import Bounds, Observation, SemanticElement
from windows_mcp.engine.sessions import SessionState
from windows_mcp.engine.target_resolver import resolve


def observe_desktop(desktop, session: SessionState, *, window: str | int | None = None,
                    limit: int = 100, max_depth: int = 8, include_offscreen: bool = False,
                    since_revision: int | None = None, target: str | None = None,
                    targets: list[str] | None = None, mode: str = "full", detail: str = "control",
                    text=None, max_chars: int = 4000, strategy: str = "bounded") -> dict:
    if not 1 <= limit <= 300 or not 1 <= max_depth <= 20 or not 1 <= max_chars <= 10000:
        raise ValueError("limit must be 1..300, max_depth 1..20, max_chars 1..10000")
    if mode not in {"full", "refresh"} or detail not in {"control", "text", "selection"}:
        raise ValueError("Unknown observation mode or detail")
    if target and targets:
        raise ValueError("Use target or targets, not both")
    if targets is not None and (not 1 <= len(targets) <= 100 or mode != "refresh"):
        raise ValueError("targets requires refresh mode and 1..100 targets")
    if text is not None and detail != "text":
        raise ValueError("text range options require detail=text")
    if detail != "control" and not (target or targets):
        raise ValueError("Text reads require an explicit target or target batch")
    session.check()
    generation = session.generation
    selected = list(dict.fromkeys(targets or ([target] if target else [])))
    if mode == "refresh" and not selected and since_revision is not None:
        previous = next((obs for obs in session.observations.values() if obs.revision == since_revision), None)
        if previous:
            selected = [item.element_id for item in previous.elements]
    refreshing = mode == "refresh" or detail != "control"
    roots = [resolve(desktop, session, item, require_enabled=False) for item in selected]
    for root in roots:
        elements.describe_text_capabilities(root)
    if refreshing and not roots:
        raise ValueError("refresh requires existing targets or an available since_revision")
    if roots:
        handles = {node.window_handle for node in roots}
        if len(handles) != 1:
            raise ValueError("A refresh batch must belong to one window")
        windows = elements.resolve_windows(str(roots[0].window_handle))
        if window is not None:
            requested = elements.resolve_windows(window)
            if len(requested) != 1 or requested[0].handle != roots[0].window_handle:
                raise elements.StaleTarget("Target does not belong to the requested window")
    else:
        windows = elements.resolve_windows(window)
    if len(windows) != 1:
        if not windows:
            raise elements.StaleTarget("TARGET_STALE: no window matches; observe open windows again")
        raise elements.AmbiguousTarget("AMBIGUOUS_TARGET: multiple windows match; supply a window handle")
    info = windows[0]
    base_scope = f"uia:{info.handle}:{info.pid}"
    diagnostics, truncated = [], False
    if refreshing:
        found = roots
        refs = sorted(node.ref for node in roots)
        scope = base_scope + ":refresh:" + hashlib.sha256(json.dumps([refs, detail, text, max_chars], sort_keys=True).encode()).hexdigest()[:20]
        coverage = "known_targets_only"
    else:
        options = {"root_element": roots[0]} if roots else {}
        if strategy != "bounded":
            options["strategy"] = strategy
        found, truncated, diagnostics = elements.walk_bounded(
            info, limit=max(1, limit - bool(roots)), max_depth=max_depth,
            include_offscreen=include_offscreen, **options
        )
        if roots:
            found = ([roots[0]] + found)[:limit]
        coverage = "subtree" if roots else "window"
        scope = base_scope + (f":subtree:{roots[0].ref}" if roots else "")
    extras = {}
    if detail != "control":
        from windows_mcp.desktop.text_ranges import read

        for node in found:
            extras[id(node)] = read(node, selection=detail == "selection", spec=text, max_chars=max_chars)
    session.check(generation)
    foreground = elements.foreground_root() == info.handle
    with session.lock:
        previous = session.observations.get(scope)
        signatures = json.dumps([info.title, foreground, limit, max_depth, include_offscreen, strategy,
            [(e.runtime_id, e.name, e.rect, e.enabled, e.offscreen, e.focused, e.automation_id,
              sorted(e.capabilities), e.state, extras.get(id(e))) for e in found]], sort_keys=True)
        if previous is not None and signatures == getattr(previous, "_signature", None):
            revision = previous.revision
        else:
            session.revision += 1
            revision = session.revision
        nodes, records = [], {}
        for element in found:
            ref = desktop.element_registry.register(element)
            target_id = f"uia:{session.session_id}:{revision}:{ref}"
            state = {key: (value[:1000] if isinstance(value, str) else value)
                     for key, value in element.state.items() if not element.password or key != "value"}
            state.update(focused=element.focused, sensitive=element.password, **extras.get(id(element), {}))
            nodes.append(SemanticElement(
                element_id=target_id, role=element.control_type, name=element.name[:300],
                automation_id=element.automation_id[:200], enabled=element.enabled,
                visible=not element.offscreen, bounds=Bounds(left=element.rect[0], top=element.rect[1],
                                                          right=element.rect[2], bottom=element.rect[3]),
                state=state, actions=elements.supported_actions(element), capabilities=sorted(element.capabilities),
            ))
            records[target_id] = {"scope": scope, "ref": ref, "handle": info.handle,
                                  "pid": info.pid, "runtime_id": element.runtime_id}
        observation = Observation(
            observation_id=f"obs_{uuid.uuid4().hex}", session_id=session.session_id,
            revision=revision, context={"source": "uia", "window_id": str(info.handle),
                "process_id": info.pid, "title": info.title[:300], "foreground": foreground,
                "coverage": coverage, "strategy": strategy, "detail": detail,
                "structural_changes_checked": not refreshing},
            elements=nodes, truncated=truncated, diagnostics=diagnostics,
        )
        observation._signature = signatures
        result = observation.model_dump()
        if since_revision is not None:
            if previous and since_revision == previous.revision:
                prior = {item.element_id.rsplit(":", 1)[-1]: item for item in previous.elements}
                current = {item.element_id.rsplit(":", 1)[-1]: item for item in nodes}
                result["elements"] = [item.model_dump() for ref, item in current.items()
                    if ref not in prior or prior[ref].model_dump(exclude={"element_id"}) != item.model_dump(exclude={"element_id"})]
                result["removed"] = [prior[ref].element_id for ref in prior.keys() - current.keys()]
                result["context"].update(delta=True, base_revision=since_revision, refresh_targets=revision != since_revision)
            else:
                result["diagnostics"].append("Requested revision is unavailable for this scope; returning a full observation")
        session.store(scope, observation, records)
    return result
