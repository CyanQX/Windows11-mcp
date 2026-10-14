"""Exercise the installed executable through an actual stdio MCP client."""

import argparse
import asyncio
import json
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


async def check(command: str) -> dict:
    transport = StdioTransport(command=command, args=["serve", "--transport", "stdio"],
        env={"ANONYMIZED_TELEMETRY": "false", "WINDOWS_MCP_WATCHDOG": "off", "WINDOWS_MCP_DISABLE_FLASH": "1"})
    async with Client(transport) as client:
        tools = await client.list_tools()
        expected = {tool["name"] for tool in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["tools"]}
        assert {tool.name for tool in tools} == expected
        session = await client.call_tool("Session", {"operation": "status"})
        blocked = await client.call_tool("PowerShell", {"command": "Write-Output upgrade-policy-smoke-only"}, raise_on_error=False)
        assert blocked.is_error
        assert "BLOCKED_BY_POLICY" in " ".join(getattr(part, "text", "") for part in blocked.content)
        return {"installed_command": command, "tool_count": len(tools), "policy_enforcement": "VERIFIED",
                "session_status": "READY" if not session.is_error else "FAILED", "transport": "stdio"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    args = parser.parse_args()
    result = asyncio.run(check(args.command))
    path = ROOT / "reports" / "installation-smoke.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
