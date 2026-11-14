"""Registration helpers shared by the tool modules."""

from __future__ import annotations

from typing import Any, Callable, Literal

from mcp.types import ToolAnnotations

from windows_mcp.infrastructure import with_analytics

Access = Literal["read", "write", "adjust"]

# read: observes only. write: changes state and may be destructive. adjust: changes state but
# is safe to repeat (clipboard, scrolling).
_ACCESS: dict[str, dict[str, bool]] = {
    "read": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    "write": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    "adjust": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
}
_UNSET: Any = object()


def tool_hints(
    access: Access,
    title: str | None = None,
    *,
    open_world: bool = False,
    idempotent: bool | None = _UNSET,
) -> ToolAnnotations:
    """MCP annotations for one access class. ``idempotent=None`` leaves that hint unset."""
    fields: dict[str, Any] = dict(_ACCESS[access], openWorldHint=open_world)
    if idempotent is not _UNSET:
        if idempotent is None:
            del fields["idempotentHint"]
        else:
            fields["idempotentHint"] = idempotent
    if title is not None:
        fields["title"] = title
    return ToolAnnotations(**fields)


def declare_tool(
    mcp: Any,
    get_analytics: Callable[[], Any],
    name: str,
    *,
    description: str,
    access: Access,
    analytics: str,
    title: bool = True,
    open_world: bool = False,
    idempotent: bool | None = _UNSET,
    offload: Literal["desktop", "background"] = "desktop",
    **options: Any,
) -> Callable[[Callable], Any]:
    """Decorator registering a tool with its annotations and analytics wrapper in one step.

    ``title=True`` titles the annotations with the tool name. Extra keyword arguments (for
    example ``output_schema``) go straight to ``mcp.tool``.
    """
    annotations = tool_hints(
        access, name if title else None, open_world=open_world, idempotent=idempotent
    )

    def decorator(func: Callable) -> Any:
        tracked = with_analytics(get_analytics(), analytics, offload=offload)(func)
        return mcp.tool(name=name, description=description, annotations=annotations, **options)(
            tracked
        )

    return decorator


def require_snapshot(desktop: Any) -> None:
    """Label-addressed tools need the element list recorded by the last Snapshot."""
    if desktop.desktop_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
