"""FastMCP assembly: lifespan, tool registration and the process-wide desktop singletons."""

from contextlib import asynccontextmanager
from typing import Any, NoReturn
import asyncio
import logging
import os
import sys

import click
from fastmcp import FastMCP

from windows_mcp.instructions import instructions

logger = logging.getLogger(__name__)

desktop: Any | None = None
watchdog: Any | None = None
analytics: Any | None = None
screen_size: Any | None = None
_mcp: FastMCP | None = None


def _get_desktop():
    return desktop


def _get_analytics():
    return analytics


def _watchdog_enabled() -> bool:
    """Whether the UIA focus WatchDog should run.

    Reads the ``WINDOWS_MCP_WATCHDOG`` environment variable. Disabling values
    (case-insensitive, whitespace-trimmed): ``off``, ``0``, ``false``, ``no``,
    ``disabled``. Anything else, including unset, keeps the watchdog enabled so
    the default behavior is unchanged.

    Returns:
        ``True`` when the watchdog should be started, ``False`` to skip it.
    """
    value = os.getenv("WINDOWS_MCP_WATCHDOG")
    if value is None:
        return True
    return value.strip().lower() not in {"off", "0", "false", "no", "disabled"}


def _exit_missing_dependency(exc: ModuleNotFoundError) -> NoReturn:
    """Report a missing runtime dependency on stderr and exit non-zero.

    A partially populated venv -- an interrupted `uv sync`, or one invalidated
    by an interpreter change -- leaves `windows_mcp` importable next to missing
    third-party packages. Without this guard the traceback is the only output,
    and MCP hosts do not surface it: Claude Desktop reports "Server
    disconnected" alone, so neither the failing module nor the remedy reaches
    the user. Written to stderr because stdout carries the stdio protocol
    stream. `uv sync` repairs any declared dependency, so the message points
    there rather than mapping the import name back to its distribution.
    """
    logger.debug("Startup import failed", exc_info=exc)
    click.echo(
        f"windows-mcp: cannot start -- {exc}. The environment looks partially "
        f"installed; run `uv sync` in the extension directory, then restart the client.",
        err=True,
    )
    sys.exit(1)


def build_mcp() -> FastMCP:
    """Create the MCP server instance."""
    global _mcp

    if _mcp is not None:
        return _mcp

    try:
        from windows_mcp.infrastructure import PostHogAnalytics, telemetry_enabled
        from windows_mcp.desktop.service import Desktop
        from windows_mcp.runtime import run_on_desktop
        from windows_mcp.tools import register_all
    except ModuleNotFoundError as exc:
        _exit_missing_dependency(exc)

    @asynccontextmanager
    async def lifespan(app: FastMCP):
        """Runs initialization code before the server starts and cleanup code after it shuts down."""
        global desktop, watchdog, analytics, screen_size
        from windows_mcp.engine.sessions import get_runtime

        if telemetry_enabled():
            analytics = PostHogAnalytics()

        def start_desktop():
            # Built on the desktop thread, so the UI Automation client lives on the thread
            # that every later desktop tool call runs on.
            instance = Desktop()
            dog = None
            if _watchdog_enabled():
                # Imported lazily so a disabled watchdog never loads comtypes.
                from windows_mcp.watchdog.service import WatchDog

                dog = WatchDog()
                dog.set_focus_callback(instance.tree.on_focus_change)
            return instance, dog

        desktop, watchdog = await run_on_desktop(start_desktop)
        get_runtime().watchdog = watchdog
        screen_size = desktop.get_screen_size()
        if watchdog is None:
            logger.debug("WatchDog disabled via WINDOWS_MCP_WATCHDOG")

        async def cleanup_idle_sessions():
            while True:
                await asyncio.sleep(30)
                await get_runtime().sweep()

        cleanup_task = asyncio.create_task(cleanup_idle_sessions())

        try:
            if watchdog:
                watchdog.start()
            logger.debug("Server started, entering main loop")
            yield
        finally:
            logger.debug("Shutting down: stopping watchdog and analytics")
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
            await get_runtime().close()
            get_runtime().watchdog = None
            if watchdog:
                await asyncio.to_thread(watchdog.stop)
            if analytics:
                await analytics.close()

    from importlib.metadata import version
    _mcp = FastMCP(name="windows-mcp", version=version("windows-mcp"), instructions=instructions, lifespan=lifespan)
    register_all(_mcp, get_desktop=_get_desktop, get_analytics=_get_analytics)
    return _mcp


def reset_server() -> None:
    """Drop the cached server so the next build registers tools against the current policy."""
    global _mcp
    _mcp = None


def __getattr__(name: str):
    if name in {"state_tool", "screenshot_tool"}:
        build_mcp()
        from windows_mcp.tools import snapshot

        tool = getattr(snapshot, name)
        if tool is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return getattr(tool, "fn", tool)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
