"""tools subpackage — registers all MCP tool definitions on a FastMCP instance."""

from windows_mcp.tools import (
    app,
    clipboard,
    display,
    element,
    filesystem,
    input,
    multi,
    notification,
    process,
    registry,
    scrape,
    shell,
    semantic,
    snapshot,
)

_MODULES = [
    app,
    display,
    shell,
    filesystem,
    snapshot,
    element,
    input,
    scrape,
    multi,
    clipboard,
    process,
    notification,
    registry,
    semantic,
]


def register_all(mcp, *, get_desktop, get_analytics, catalog_only: bool = False):
    """Register every tool module on *mcp*.

    *get_desktop* and *get_analytics* are zero-arg callables that return the
    current ``Desktop`` and ``PostHogAnalytics`` instances (resolved lazily so
    that tools can be registered before ``lifespan`` initializes the singletons).
    ``catalog_only`` registers signatures for schema discovery without touching
    live runtime dispatch state; that in-memory server must never execute tools.
    """
    from windows_mcp.tools.gateway import ToolGateway

    gateway = mcp if catalog_only else ToolGateway(mcp, get_desktop=get_desktop)
    previous = snapshot.state_tool, snapshot.screenshot_tool
    try:
        for mod in _MODULES:
            mod.register(gateway, get_desktop=get_desktop, get_analytics=get_analytics)
    finally:
        if catalog_only:
            snapshot.state_tool, snapshot.screenshot_tool = previous
