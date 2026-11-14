"""``RequestApproval`` tool logic: derive scope and risk, then trust the call or ask the operator."""

from __future__ import annotations

from windows_mcp.runtime import call_on_desktop


def request_approval(runtime, tool: str, arguments: dict, description: str) -> dict:
    """Return TRUSTED when local trust already covers the call, else file a signed request."""
    normalized = runtime.normalize(tool, arguments)
    scope = call_on_desktop(runtime.scope_resolver, tool, normalized, runtime.current())
    risk = runtime.policy.risk(tool, normalized, scope)
    network_origins = runtime.policy.network_origins(tool, normalized, scope)
    if runtime.policy.trust and runtime.policy.trust.automatic() and (risk < 3 or runtime.policy.allow_r3):
        return {"status": "TRUSTED", "risk": risk, "approval_required": False,
                "authorization_scope": runtime.policy.trust.status(),
                "operator_guidance": "本机持续全自动已开启，可直接执行；Session.full_access 表示是否已开启完全访问。重连、重启后持续有效，直到操作员手动关闭。"}
    if risk == 2 and runtime.policy.trusted(runtime.current().session_id, tool, normalized, scope) and not network_origins:
        return {"status": "TRUSTED", "risk": risk, "approval_required": False,
                "authorization_scope": scope,
                "operator_guidance": "本机操作员已授权此客户端会话和应用范围：可直接调用匹配工具；其他应用和出网站点仍单独授权。"}
    request = runtime.policy.approvals.request(runtime.current().session_id, tool, normalized, risk, description,
                                              authorization_scope=scope, network_origins=network_origins)
    from windows_mcp.infrastructure.operator_ui import notify_operator
    notified = notify_operator(request["request_id"])
    return {"status": "NEEDS_APPROVAL", "request_id": request["request_id"], "risk": risk,
            "expires_at": request["expires_at"], "scope": request["scope"],
            "authorization_scope": scope, "network_origins": network_origins,
            "operator_ui_opened": notified,
            "operator_guidance": "由本机操作员点击「批准一次」；或点击「持续全自动」立即开启完全访问，直到手动关闭。AI不能代为批准或操作审批设置。",
            "operator_command": f"windows-mcp approve {request['request_id']}"}
