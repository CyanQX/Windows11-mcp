"""The real server (lifespan, registration, schemas) driven by an MCP client over the protocol."""

from __future__ import annotations

import re

from fastmcp import Client

from windows_mcp.__main__ import _build_mcp

EXPECTED_TOOLS = {
    "App", "PowerShell", "FileSystem", "Screenshot", "Snapshot", "Find", "Act", "Click", "Type",
    "Scroll", "Move", "Shortcut", "Wait", "WaitFor", "DisplayInventory", "Scrape", "MultiSelect",
    "MultiEdit", "Clipboard", "Process", "Notification", "Registry",
}  # fmt: skip


def _text(result) -> str:
    return "\n".join(part.text for part in result.content if getattr(part, "text", None))


async def test_find_and_act_through_the_mcp_protocol(app):
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
        acted = _text(await client.call_tool("Act", {"target": ref, "action": "click"}))
        assert "real left click" in acted
        assert app.wait_for(lambda e: e.get("control") == "save", since)

        missing = await client.call_tool(
            "Act", {"target": "No Such Button", "window": app.title}, raise_on_error=False
        )
        assert missing.is_error and "No control named" in _text(missing)
