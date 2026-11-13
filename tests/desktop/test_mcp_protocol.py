"""The real server (lifespan, registration, schemas) driven by an MCP client over the protocol."""

from __future__ import annotations

import re
import json

import pytest
from fastmcp import Client

from windows_mcp.__main__ import _build_mcp

EXPECTED_TOOLS = {
    "App", "PowerShell", "FileSystem", "Screenshot", "Snapshot", "Find", "Act", "Click", "Type",
    "Scroll", "Move", "Shortcut", "Wait", "WaitFor", "DisplayInventory", "Scrape", "MultiSelect",
    "MultiEdit", "Clipboard", "Process", "Notification", "Registry",
}  # fmt: skip


def _text(result) -> str:
    return "\n".join(part.text for part in result.content if getattr(part, "text", None))


@pytest.mark.parametrize("via", ["input", "uia"])
async def test_find_and_act_through_the_mcp_protocol(app, via):
    async with Client(_build_mcp()) as client:
        instructions = getattr(client, "instructions", None) or client.initialize_result.instructions
        assert "OBSERVE -> ACT -> VERIFY" in (instructions or "")
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert EXPECTED_TOOLS <= set(tools)
        act_tool = tools["Act"]
        schema = getattr(act_tool, "input_schema", None) or act_tool.inputSchema  # mcp 2 / mcp 1
        act_schema = schema["properties"]
        assert set(act_schema["via"]["enum"]) == {"auto", "input", "uia"}

        found = _text(await client.call_tool("Find", {"name": "Save", "window": app.title}))
        ref = re.search(r"^(e\d+)\s+Button \"Save\"", found, re.M).group(1)
        since = app.mark()
        arguments = {"target": ref, "action": "click", "via": via}
        approval = await client.call_tool("RequestApproval", {"tool": "Act", "arguments": arguments, "description": "Click the owned test fixture button"})
        import json
        from windows_mcp.engine.sessions import get_runtime

        get_runtime().policy.approvals.approve(json.loads(_text(approval))["request_id"])
        result = await client.call_tool("Act", arguments, raise_on_error=False)
        acted = _text(result)
        if via == "input" and result.is_error and "INPUT_TARGET_UNREACHABLE" in acted:
            assert not app.wait_for(lambda e: e.get("control") == "save", since, timeout=0.1)
            pytest.skip("Input desktop refused cursor movement; no mouse button was pressed")
        assert not result.is_error, acted
        assert ("real left click" if via == "input" else "UIA Invoke") in acted
        assert app.wait_for(lambda e: e.get("control") == "save", since)

        approval = await client.call_tool("RequestApproval", {"tool": "Act", "arguments": {"target": "No Such Button", "window": app.title}, "description": "Prove a nonexistent target safely fails"}, raise_on_error=False)
        assert approval.is_error and "No control named" in _text(approval)
        missing = await client.call_tool(
            "Act", {"target": "No Such Button", "window": app.title}, raise_on_error=False
        )
        assert missing.is_error and "No control named" in _text(missing)


async def test_persistent_automatic_clicks_survive_new_mcp_clients_and_revoke(app):
    from tests.desktop.guard import assert_point_owned
    from windows_mcp.engine.sessions import get_runtime

    runtime = get_runtime()
    runtime.policy.trust.grant_automatic(operator="isolated-desktop-acceptance")
    sessions = set()
    try:
        for number in range(2):
            async with Client(_build_mcp()) as client:
                state = json.loads(_text(await client.call_tool("Session")))
                assert state["trust_mode"] == "automatic"
                assert state["trust_scope"]["expires_at"] is None
                sessions.add(state["session_id"])
                found = _text(await client.call_tool("Find", {"name": "Save", "window": app.title}))
                ref = re.search(r"^(e\d+)\s+Button \"Save\"", found, re.M).group(1)
                arguments = {"target": ref, "action": "click", "via": "input"}
                approval = json.loads(_text(await client.call_tool("RequestApproval", {
                    "tool": "Act", "arguments": arguments, "description": "Click only the owned fixture"})))
                assert approval["status"] == "TRUSTED" and not approval["approval_required"]
                assert runtime.policy.approvals.pending() == []
                assert_point_owned(*app.center("save"), {app.pid})
                since = app.mark()
                acted = await client.call_tool("Act", arguments, raise_on_error=False)
                assert not acted.is_error, _text(acted)
                assert "real left click" in _text(acted)
                received = app.wait_for(lambda event: event.get("control") == "save", since)
                assert received and received[-1]["count"] == number + 1
                if number == 1:
                    runtime.policy.trust.revoke()
                    since = app.mark()
                    denied = await client.call_tool("Act", arguments, raise_on_error=False)
                    assert denied.is_error and "BLOCKED_BY_POLICY" in _text(denied)
                    assert not app.wait_for(lambda event: event.get("control") == "save", since, timeout=0.1)
        assert len(sessions) == 2
    finally:
        runtime.policy.trust.revoke()
