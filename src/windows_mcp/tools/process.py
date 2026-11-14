"""Process tool — list and kill running processes."""

from functools import partial
from typing import Literal

from fastmcp import Context
from windows_mcp import process
from windows_mcp.coercion import truthy
from windows_mcp.tools._common import declare_tool


def register(mcp, *, get_desktop, get_analytics):
    tool = partial(declare_tool, mcp, get_analytics)

    @tool(
        "Process",
        description='List and kill running processes. Keywords: task manager, running tasks, kill, terminate, stop process, PID, CPU, memory usage. Use mode="list" to list running processes with filtering and sorting options. Use mode="kill" to terminate processes by PID or name.',
        access="write",
        analytics="Process-Tool",
        offload="background",
    )
    def process_tool(
        mode: Literal["list", "kill"],
        name: str | None = None,
        pid: int | None = None,
        sort_by: Literal["memory", "cpu", "name"] = "memory",
        limit: int = 20,
        force: bool | str = False,
        ctx: Context = None,
    ) -> str:
        try:
            if mode == "list":
                return process.list_processes(name=name, sort_by=sort_by, limit=limit)
            elif mode == "kill":
                return process.kill_process(name=name, pid=pid, force=truthy(force))
            else:
                return 'Error: mode must be either "list" or "kill".'
        except Exception:
            raise
