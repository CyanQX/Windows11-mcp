"""Fixed tool sets. A profile limits discovery; PolicyGate still checks every call."""

import logging

logger = logging.getLogger(__name__)

PROFILES = {
    "full": None,
    "ui-core": frozenset({"Observe", "Execute", "Verify", "Watch", "Session", "RequestApproval",
                          "Visual", "DisplayInventory", "App", "Wait", "WaitFor", "Find"}),
    "browser": frozenset({"Browser", "Observe", "Session", "RequestApproval", "Wait"}),
}


def select_tools(profile: str, registered: set[str], explicit=None, exclude=None) -> list[str]:
    if profile not in PROFILES:
        raise ValueError("Unknown tool profile")
    selected = registered if PROFILES[profile] is None else registered & PROFILES[profile]
    unknown = (set(explicit or []) | set(exclude or [])) - registered
    if unknown:
        raise ValueError(f"Unknown tool names: {sorted(unknown)}")
    if explicit:
        # Explicit tools can narrow a profile, never re-add tools excluded by it.
        selected = selected & set(explicit)
    elif exclude:
        selected = selected - set(exclude)
    if not selected:
        raise ValueError("Tool selection is empty")
    return sorted(selected)


def apply_tool_filter(
    mcp, explicit_tools: list[str] | None, exclude_tools: list[str] | None
) -> None:
    """Remove disabled tools from the MCP registry."""
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools_dict = getattr(tool_mgr, "_tools", None)
    if tools_dict is None:
        provider = getattr(mcp, "_local_provider", None)
        components = getattr(provider, "_components", {})
        tools_dict = {
            (getattr(v, "name", None) or k.split(":", 1)[1].split("@", 1)[0]): k
            for k, v in components.items()
            if isinstance(k, str) and k.startswith("tool:")
        }

        def _remove(name):
            keys = [
                k
                for k, v in components.items()
                if isinstance(k, str)
                and k.startswith("tool:")
                and (
                    getattr(components[k], "name", None) == name
                    or k.split(":", 1)[1].split("@", 1)[0] == name
                )
            ]
            for k in keys:
                components.pop(k, None)

        registered = set(tools_dict.keys())
    else:

        def _remove(name):
            tools_dict.pop(name, None)

        registered = set(tools_dict.keys())

    if explicit_tools:
        keep = {t for t in explicit_tools if t in registered}
        for name in registered - keep:
            _remove(name)
    elif exclude_tools:
        for name in exclude_tools:
            if name in registered:
                _remove(name)
    logger.debug("Tool filter applied: explicit=%s exclude=%s", explicit_tools, exclude_tools)
