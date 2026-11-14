"""Lazy public services: small installer imports must not initialize the MCP SDK."""

from importlib import import_module

_EXPORTS = {
    "auth": ("AuthKeyMiddleware", "OAuthOnlyMiddleware", "is_loopback_host"),
    "security": ("IPAllowlistMiddleware", "parse_ip_allowlist", "validate_url"),
    "analytics": ("Analytics", "PostHogAnalytics", "telemetry_enabled", "with_analytics"),
    "config": ("WindowsMCPConfig", "ServerConfig", "SecurityConfig", "ToolsConfig",
               "CONFIG_DIR", "CONFIG_FILE", "discover_config_path", "load_config", "write_config"),
    "oauth": ("OAuthStore", "build_oauth_routes", "validate_oauth_token"),
}
_MODULES = {name: module for module, names in _EXPORTS.items() for name in names}
__all__ = list(_MODULES)


def __getattr__(name):
    module = _MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
