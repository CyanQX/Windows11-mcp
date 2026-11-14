"""``Session`` tool logic: cancel/resume/acknowledge plus the status report."""

from __future__ import annotations

import asyncio

from windows_mcp.engine.contracts import Status


async def run_session_operation(runtime, session, operation: str, action_id: str | None) -> dict:
    """Apply one Session operation, then report this client's state."""
    if operation == "cancel":
        session.cancel()
        if runtime.browser:
            await runtime.browser.close_session(session.session_id)
    elif operation == "resume":
        session.cancelled.clear()
        session.pointer_interruption = None
    elif operation == "acknowledge":
        runtime.pending.acknowledge(session.session_id, action_id)
    trust = runtime.policy.trust.status() if runtime.policy.trust else None
    if trust and trust.get("scope") != "installation" and trust.get("session_id") != session.session_id:
        trust = None
    from windows_mcp.vision.native_bridge import capabilities
    from windows_mcp.infrastructure.execution_security import runtime_status
    privilege = runtime_status()
    native = await asyncio.to_thread(capabilities) if operation == "status" and privilege["allowed"] else {"probe": "skipped", "reason": privilege.get("code", "control_operation")}
    from windows_mcp.desktop.coordination import status as desktop_status
    from windows_mcp.vision.capture_worker import worker

    return {"status": Status.CANCELLED.value if session.cancelled.is_set() else "READY",
            "execution_privilege": privilege,
            "desktop_coordination": desktop_status(), "capture_worker": worker.status(),
            "native_components": native,
            "session_id": session.session_id, "revision": session.revision,
            "interaction_mode": runtime.interaction_mode,
            "pointer_motion": runtime.motion.style, "hover_ms": runtime.motion.hover_ms,
            "human_takeover": runtime.motion.human_takeover and runtime.motion.style == "natural",
            "pointer_interruption": session.pointer_interruption,
            "last_pointer_motion": session.last_pointer_motion,
            "workspace_roots": [str(root) for root in runtime.policy.roots],
            "full_access": runtime.policy.trust.full_access(),
            "r3_enabled": runtime.policy.allow_r3, "pending_action": runtime.pending.status(),
            "trust_mode": (trust or {}).get("mode"), "trust_scope": trust,
            "network_grants": runtime.policy.network.status(session.session_id),
            "allowed_network_origins": sorted(runtime.policy.allowed_network_origins)}
