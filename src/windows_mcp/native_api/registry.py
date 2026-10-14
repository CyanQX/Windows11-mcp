"""Static provider whitelist: MCP arguments cannot register arbitrary Python/COM code."""

from windows_mcp.native_api import filesystem


def run(policy, provider: str, operation: str, **arguments) -> dict:
    if provider != "filesystem":
        raise ValueError("Unsupported provider; this build enables only filesystem")
    return filesystem.run(policy, operation, **arguments)
