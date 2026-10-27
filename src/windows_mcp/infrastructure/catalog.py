"""Capture actual MCP initialize/tools-list responses for a fixed profile and SDK generation."""

from __future__ import annotations

import json
import inspect
from importlib.metadata import version

from fastmcp import Client, FastMCP

from windows_mcp.infrastructure.profiles import select_tools


def wire_model(value) -> dict:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def initialize_client_options() -> dict:
    """MCPB static initialize requires handshake-era responses on either SDK generation."""
    return {"mode": "legacy"} if "mode" in inspect.signature(Client).parameters else {}


async def snapshot(profile: str = "full") -> dict:
    from windows_mcp.__main__ import instructions, _apply_tool_filter
    from windows_mcp.tools import register_all

    # Registration mirrors the real server. No lifespan, desktop, telemetry or user mutation.
    server = FastMCP(name="windows-mcp", version=version("windows-mcp"), instructions=instructions)
    register_all(server, get_desktop=lambda: None, get_analytics=lambda: None, catalog_only=True)
    if not initialize_client_options():
        # FastMCP 3's stdio runner explicitly enables only tools_changed, while its
        # memory transport defaults to all three. Match the actual stdio handshake.
        from mcp.server.lowlevel.server import NotificationOptions

        server._mcp_server.notification_options = NotificationOptions(tools_changed=True)
    async with Client(server, **initialize_client_options()) as client:
        names = {tool.name for tool in await client.list_tools()}
    _apply_tool_filter(server, select_tools(profile, names), None)
    async with Client(server, **initialize_client_options()) as client:
        tools = await client.list_tools()
        return {"initialize": wire_model(client.initialize_result),
                "tools/list": {"tools": [wire_model(tool) for tool in tools]}}


async def export_manifest(base: dict, profile: str = "full") -> dict:
    responses = await snapshot(profile)
    result = json.loads(json.dumps(base))
    result["name"] = responses["initialize"]["serverInfo"]["name"]
    result["version"] = responses["initialize"]["serverInfo"]["version"]
    result["tools"] = [{"name": item["name"], "description": item.get("description", "")}
                       for item in responses["tools/list"]["tools"]]
    result.setdefault("_meta", {})["com.microsoft.windows"] = {"static_responses": responses}
    result["_meta"]["windows_mcp.catalog"] = {"profile": profile, "fastmcp": version("fastmcp"),
        "mcp": version("mcp"), "note": "Generated for this SDK generation; regenerate when schemas or protocol responses change. No ODR registration or MSIX identity is implied."}
    if "server" in result:
        config = result["server"].get("mcp_config", {})
        args = config.get("args", [])
        if "serve" in args:
            config["args"] = args[:args.index("serve") + 1] + ["--profile", profile]
    return result


def validate_snapshot(manifest: dict, actual: dict, *, profile: str) -> None:
    expected = manifest.get("_meta", {}).get("com.microsoft.windows", {}).get("static_responses")
    if expected is None or manifest.get("_meta", {}).get("windows_mcp.catalog", {}).get("profile") != profile:
        raise ValueError("Static manifest does not declare this fixed profile")
    if expected != actual:
        raise ValueError("Static initialize/tools-list responses differ from this runtime; regenerate with the active SDK and profile")
