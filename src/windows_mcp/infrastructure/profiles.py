"""Fixed tool sets. A profile limits discovery; PolicyGate still checks every call."""

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
