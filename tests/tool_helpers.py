"""Shared doubles for tests that register tool modules without a real FastMCP server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakeMCP:
    """Stands in for ``FastMCP`` and records every ``@mcp.tool`` registration.

    ``tools`` maps a tool name to its registered callable; ``tool_options`` maps it to the
    keyword arguments (description, annotations, ...) the module passed to ``mcp.tool``.
    """

    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}
        self.tool_options: dict[str, dict[str, Any]] = {}

    @property
    def options(self) -> dict[str, dict[str, Any]]:
        return self.tool_options

    def tool(self, *, name: str, **kwargs: Any) -> Callable[[Callable], Callable]:
        self.tool_options[name] = kwargs

        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def register_tools(register: Callable[..., None], desktop: Any = None) -> dict[str, Callable]:
    """Run one tool module's ``register`` against a fake server and return its tools."""
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools
