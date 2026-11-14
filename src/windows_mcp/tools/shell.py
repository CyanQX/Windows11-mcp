"""PowerShell tool — shell/command execution."""

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp.powershell import PowerShellExecutor
from fastmcp import Context
from typing import Literal
from pathlib import Path


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="PowerShell",
        description="Legacy PowerShell command execution under a verified non-elevated user token. No UAC, Administrator, SYSTEM or installer-maintenance privileges. Bounded output and process-tree timeout. Prefer Terminal for separate stdout/stderr, exit codes, interactive sessions and cancellation.",
        annotations=ToolAnnotations(
            title="PowerShell",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "Powershell-Tool", offload="background")
    def powershell_tool(command: str, timeout: int = 30, ctx: Context = None) -> str:
        try:
            response, status_code = PowerShellExecutor.execute_command(command, timeout)
            return f"Response: {response}\nStatus Code: {status_code}"
        except Exception:
            raise

    @mcp.tool(name="Terminal", description="Run non-elevated powershell.exe, installed pwsh.exe or cmd.exe. execute returns bounded stdout/stderr, exit_code, cwd, duration, timeout/cancellation. start/write/read/cancel/close manage a client-owned shell; write includes a newline when submitting a command. Use App to open wt.exe (Windows Terminal host), then Observe/Execute for its UI; its host is not an output-capture sandbox. No elevation or privileged maintenance. Output may contain secrets; do not request credentials.", annotations=ToolAnnotations(title="Terminal", readOnlyHint=False, destructiveHint=True, openWorldHint=True))
    @with_analytics(get_analytics(), "Terminal-Tool", offload="background")
    def terminal_tool(operation: Literal["execute", "start", "write", "read", "cancel", "close"] = "execute",
                      shell: Literal["powershell", "pwsh", "cmd"] = "powershell", command: str = "",
                      cwd: str | None = None, timeout: float = 30, terminal_id: str | None = None,
                      text: str = "", encoding: Literal["auto", "utf-8", "utf-16le", "oem", "ansi"] = "auto",
                      include_raw: bool = False, ctx: Context = None) -> dict:
        from windows_mcp.engine.sessions import get_runtime
        from windows_mcp.terminal import execute, Terminals
        runtime = get_runtime()
        state = runtime.current()
        if operation == "execute":
            return execute(command, shell=shell, cwd=cwd, timeout=timeout, cancelled=state.cancelled, encoding=encoding, include_raw=include_raw)
        if runtime.terminals is None:
            runtime.terminals = Terminals()
        manager = runtime.terminals
        if operation == "start":
            return manager.start(state.session_id, shell, cwd or str(Path.home()))
        item = manager.get(state.session_id, terminal_id)
        if operation == "write":
            item.write(text)
        elif operation == "cancel":
            item.stop()
        elif operation == "close":
            return manager.close(state.session_id, terminal_id)
        return item.status(encoding=encoding, include_raw=include_raw)
