"""Actual frozen MCP privilege/terminal acceptance in one disposable user profile."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def environment(home):
    from windows_mcp.infrastructure.process_env import independent_environment
    env = independent_environment()
    for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "DSH_HOME", "XDG_CONFIG_HOME", "OPENCODE_CONFIG"):
        env.pop(name, None)
    env.update(USERPROFILE=str(home), HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):],
               LOCALAPPDATA=str(home / "AppData/Local"), APPDATA=str(home / "AppData/Roaming"),
               ANONYMIZED_TELEMETRY="false", WINDOWS_MCP_WATCHDOG="off", WINDOWS_MCP_DISABLE_FLASH="1")
    return env


async def check(executable, root):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from windows_mcp.infrastructure.execution_security import require_standard, token_status, ExecutionDenied
    from windows_mcp.infrastructure.trust import TrustStore
    from windows_mcp.infrastructure.local_storage import private_directory

    home = root / "terminal-home"
    home.mkdir()
    authority = home / ".windows-mcp/approvals"
    private_directory(authority)
    TrustStore(authority).grant_automatic(operator="isolated-native-acceptance", full_access=True)
    state = token_status()
    elevated = False
    try:
        require_standard()
    except ExecutionDenied:
        elevated = True
    async with Client(StdioTransport(command=str(executable), args=["--serve"], env=environment(home))) as client:
        def data(value):
            if isinstance(value.data, dict):
                return value.data
            return json.loads(next(item.text for item in value.content if getattr(item, "text", None)))
        tools = await client.list_tools()
        assert len(tools) == 32 and "Terminal" in {tool.name for tool in tools}
        result = await client.call_tool("Terminal", {"command": "Write-Output actilo-native-token", "timeout": 15}, raise_on_error=False)
        if elevated:
            assert result.is_error
            state_report = data(await client.call_tool("Session", {"operation": "status"}))
            assert not state_report["execution_privilege"]["allowed"]
            assert state_report["execution_privilege"]["code"] == "NON_ELEVATED_RUNTIME_REQUIRED"
            return {"version": "1.9.10", "tool_count": len(tools), "runtime_integrity": state["integrity"],
                    "privileged_parent_refused": True, "normal_terminal_tests": "not run: runner has a privileged token"}
        assert not result.is_error, result
        row = data(result)
        assert row["exit_code"] == 0 and "actilo-native-token" in row["stdout"]
        cmd = data(await client.call_tool("Terminal", {"shell": "cmd", "command": "echo Actilo & exit /b 7", "timeout": 15}))
        assert cmd["exit_code"] == 7 and "Actilo" in cmd["stdout"]
        extra = "not installed"
        if shutil.which("pwsh.exe"):
            pwsh = data(await client.call_tool("Terminal", {"shell": "pwsh", "command": "Write-Output Actilo; exit 3", "timeout": 15}))
            assert pwsh["exit_code"] == 3 and "Actilo" in pwsh["stdout"]
            extra = "PASS"
        timeout = data(await client.call_tool("Terminal", {"command": "Start-Sleep 30", "timeout": .5}))
        assert timeout["timed_out"] and not timeout["running"]
        denied = await client.call_tool("Terminal", {"command": "Start-Process cmd -Verb RunAs"}, raise_on_error=False)
        assert denied.is_error
        return {"version": "1.9.10", "tool_count": len(tools), "runtime_integrity": state["integrity"],
                "normal_powershell": "PASS", "cmd_exit_code": "PASS", "pwsh": extra,
                "timeout_process_tree": "PASS", "explicit_elevation_refused": "PASS", "isolated_user_profile": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="actilo-runtime-check-") as directory:
        result = asyncio.run(check(args.executable.resolve(), Path(directory)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
