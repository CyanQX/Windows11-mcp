"""Command line entry point: the ``serve`` command plus the group that assembles every subcommand."""

from enum import Enum
from functools import partial
import asyncio
import logging
import os
import shlex

import click
from click.core import ParameterSource

from windows_mcp.cli.auth import auth
from windows_mcp.cli.diagnostics import connectors_command, doctor_command, manifest_command, native_helper_command
from windows_mcp.cli.grants import approve, network_grants, trust
from windows_mcp.cli.service_task import install, uninstall
from windows_mcp.config import enable_debug
from windows_mcp.infrastructure import (
    OAuthStore,
    build_oauth_routes,
    discover_config_path,
    is_loopback_host,
    load_config,
    parse_ip_allowlist,
    validate_oauth_token,
)
from windows_mcp.infrastructure.http_middleware import http_middleware
from windows_mcp.infrastructure.profiles import apply_tool_filter as _apply_tool_filter
from windows_mcp.server import build_mcp as _build_mcp
from windows_mcp.server import reset_server

logger = logging.getLogger(__name__)


def _param_explicit(ctx: click.Context, name: str) -> bool:
    src = ctx.get_parameter_source(name)
    return src in {ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT}


def _choose_value(ctx: click.Context, name: str, cli_value, config_value, default_value):
    if _param_explicit(ctx, name):
        return cli_value
    if config_value is not None:
        return config_value
    return default_value


class Transport(Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"

    def __str__(self):
        return self.value


_LEGACY_SERVE_FLAGS = {
    "--transport",
    "--host",
    "--port",
    "--auth-key",
    "--stateless-http",
    "--ip-allowlist",
    "--cors-origins",
    "--ssl-certfile",
    "--ssl-keyfile",
    "--oauth-client-id",
    "--oauth-client-secret",
    "--profile",
    "--static-manifest",
    "--tools",
    "--exclude-tools",
    "--allow-insecure-remote",
    "--debug",
}


class _LegacyAwareGroup(click.Group):
    """Detect serve flags passed to the top-level command group."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        legacy_arg = self._first_legacy_arg_before_subcommand(args)
        if legacy_arg:
            suggested = shlex.join(["windows-mcp", "serve", *args])
            raise click.UsageError(
                "`windows-mcp` is now a command group. Did you mean:\n"
                f"    {suggested}\n"
                "These flags belong on the `serve` subcommand. "
                "See `windows-mcp serve --help`.",
                ctx=ctx,
            )
        return super().parse_args(ctx, args)

    def _first_legacy_arg_before_subcommand(self, args: list[str]) -> str | None:
        commands = set(self.commands)
        for arg in args:
            if arg in commands:
                return None
            flag = arg.split("=", 1)[0]
            if flag in _LEGACY_SERVE_FLAGS:
                return arg
        return None


def _run_server(
    transport: str,
    host: str,
    port: int,
    auth_key: str | None = None,
    ip_allowlist: list | None = None,
    explicit_tools: list[str] | None = None,
    exclude_tools: list[str] | None = None,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    oauth_validator=None,
    cors_origins: list[str] | None = None,
    allowed_hosts: list[str] | None = None,
    stateless_http: bool = False,
) -> None:
    mcp = _build_mcp()
    if explicit_tools or exclude_tools:
        _apply_tool_filter(mcp, explicit_tools, exclude_tools)
    match transport:
        case Transport.STDIO.value:
            mcp.run(transport=Transport.STDIO.value, show_banner=False)
        case Transport.SSE.value | Transport.STREAMABLE_HTTP.value:
            uvicorn_config: dict = {}
            if ssl_certfile and ssl_keyfile:
                uvicorn_config["ssl_certfile"] = ssl_certfile
                uvicorn_config["ssl_keyfile"] = ssl_keyfile
            mcp.run(
                transport=transport,
                host=host,
                port=port,
                show_banner=False,
                middleware=http_middleware(
                    auth_key=auth_key,
                    ip_allowlist=ip_allowlist,
                    oauth_validator=oauth_validator,
                    cors_origins=cors_origins,
                    allowed_hosts=allowed_hosts,
                ),
                uvicorn_config=uvicorn_config or None,
                stateless_http=stateless_http,
            )
        case _:
            raise ValueError(f"Invalid transport: {transport}")


@click.group(cls=_LegacyAwareGroup, no_args_is_help=False)
def main():
    """Actilo: MCP server for Windows desktop automation (windows-mcp compatibility)."""


def _enable_debug_logging() -> None:
    enable_debug()
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastmcp"]:
        logging.getLogger(name).setLevel(logging.DEBUG)


def _configure_runtime(cfg, interaction_mode: str, pointer_motion: str) -> None:
    """Install the policy gate and pointer settings, then drop any server built under an old one."""
    from pathlib import Path
    from windows_mcp.desktop.pointer_motion import MotionSettings
    from windows_mcp.engine.sessions import configure_runtime
    from windows_mcp.infrastructure.policy import PolicyGate

    configure_runtime(PolicyGate(roots=[Path(root) for root in cfg.policy.workspace_roots] or None,
                                 allow_r3=cfg.policy.allow_r3,
                                 allow_private_browser=cfg.policy.allow_private_browser,
                                 allowed_network_origins=cfg.policy.allowed_network_origins),
                      interaction_mode=interaction_mode,
                      motion=MotionSettings(pointer_motion, cfg.interaction.hover_ms, cfg.interaction.human_takeover))
    reset_server()  # A cached server must never retain a gateway from a previous policy configuration.


def _resolve_tool_selection(ctx, cfg, *, transport, tools, exclude_tools, profile, static_manifest):
    """Merge --tools/--exclude-tools/--profile with config.toml; returns (explicit, excluded)."""
    cli_tools = [t.strip() for t in tools.split(",") if t.strip()] if tools else []
    cli_exclude = (
        [t.strip() for t in exclude_tools.split(",") if t.strip()]
        if _param_explicit(ctx, "exclude_tools") and exclude_tools
        else list(cfg.tools.exclude)
    )
    profile = _choose_value(ctx, "profile", profile, cfg.tools.profile, "full")
    if static_manifest:
        if transport != "stdio" or cli_tools or cli_exclude:
            raise click.ClickException("Static manifests require stdio and the exact profile without custom tool filters")
        from windows_mcp.infrastructure.catalog import snapshot, validate_snapshot
        import json
        from pathlib import Path

        try:
            validate_snapshot(json.loads(Path(static_manifest).read_text(encoding="utf-8")),
                              asyncio.run(snapshot(profile)), profile=profile)
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
    if profile != "full":
        from windows_mcp.infrastructure.profiles import PROFILES

        allowed = PROFILES[profile]
        cli_tools = sorted((allowed & set(cli_tools)) if cli_tools else (allowed - set(cli_exclude)))
        cli_exclude = []
        if not cli_tools:
            raise click.ClickException("Tool profile selection is empty")
    return cli_tools, cli_exclude


def _allowed_hosts(transport: str, host: str, allow_insecure_remote: bool) -> list[str] | None:
    """DNS rebinding protection: validate the Host header against the bind address.

    Applied automatically for loopback binds; skipped for 0.0.0.0/:: (too broad) and when
    allow_insecure_remote is set.
    """
    if transport == Transport.STDIO.value or allow_insecure_remote:
        return None
    if is_loopback_host(host):
        return ["localhost", "127.0.0.1", "[::1]"]
    if host not in ("0.0.0.0", "::", ""):
        return [host]
    return None


def _install_oauth(client_id: str, client_secret: str, *, host: str, port: int, tls: bool):
    """Mount the OAuth routes on the server and return the bearer-token validator."""
    mcp = _build_mcp()
    oauth_store = OAuthStore()
    scheme = "https" if tls else "http"
    issuer = f"{scheme}://{host}:{port}"
    routes = build_oauth_routes(
        store=oauth_store,
        issuer=issuer,
        configured_client_id=client_id,
        configured_client_secret=client_secret,
    )
    for path, (handler, methods) in routes.items():
        mcp.custom_route(path, methods=methods)(handler)
    return lambda token: validate_oauth_token(oauth_store, token)


@main.command()
@click.pass_context
@click.option(
    "--transport",
    help="The transport layer used by the MCP server.",
    type=click.Choice(
        [Transport.STDIO.value, Transport.SSE.value, Transport.STREAMABLE_HTTP.value]
    ),
    default="stdio",
)
@click.option(
    "--host",
    help="Host to bind the SSE/Streamable HTTP server.",
    default="localhost",
    type=str,
    show_default=True,
)
@click.option(
    "--port",
    help="Port to bind the SSE/Streamable HTTP server.",
    default=8000,
    type=int,
    show_default=True,
)
@click.option(
    "--debug",
    help="Enable debug mode to provide verbose logging for troubleshooting.",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--config",
    help="Path to windows-mcp config file (default: ~/.windows-mcp/config.toml).",
    default=None,
    type=click.Path(dir_okay=False),
    show_default=False,
)
@click.option(
    "--auth-key",
    help="Bearer token required on all HTTP requests. Can also be set via WINDOWS_MCP_AUTH_KEY.",
    default=None,
    envvar="WINDOWS_MCP_AUTH_KEY",
    type=str,
    show_default=False,
)
@click.option(
    "--allow-insecure-remote",
    help="Allow binding to non-loopback addresses without authentication (not recommended).",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--ip-allowlist",
    help="Comma-separated list of allowed client IPs or CIDR ranges (e.g. '10.0.0.0/8,192.168.1.5'). IPv4 and IPv6 supported.",
    default=None,
    envvar="WINDOWS_MCP_IP_ALLOWLIST",
    type=str,
    show_default=False,
)
@click.option(
    "--tools",
    help="Comma-separated explicit list of tools to enable (e.g. 'Screenshot,Click,Snapshot'). Overrides --exclude-tools.",
    default=None,
    envvar="WINDOWS_MCP_TOOLS",
    type=str,
    show_default=False,
)
@click.option(
    "--exclude-tools",
    help="Comma-separated list of tools to remove from the active set (e.g. 'PowerShell,Registry').",
    default=None,
    envvar="WINDOWS_MCP_EXCLUDE_TOOLS",
    type=str,
    show_default=False,
)
@click.option(
    "--cors-origins",
    help="Comma-separated list of allowed CORS origins (e.g. 'https://my-client.example.com'). Defaults to none — no CORS headers are emitted, so browsers block cross-origin requests via Same-Origin Policy.",
    default=None,
    envvar="WINDOWS_MCP_CORS_ORIGINS",
    type=str,
    show_default=False,
)
@click.option(
    "--ssl-certfile",
    help="Path to TLS certificate file (.pem) for HTTPS. Requires --ssl-keyfile.",
    default=None,
    envvar="WINDOWS_MCP_SSL_CERTFILE",
    type=str,
    show_default=False,
)
@click.option(
    "--ssl-keyfile",
    help="Path to TLS private key file (.pem) for HTTPS. Requires --ssl-certfile.",
    default=None,
    envvar="WINDOWS_MCP_SSL_KEYFILE",
    type=str,
    show_default=False,
)
@click.option(
    "--oauth-client-id",
    help="OAuth client ID (pre-provisioned confidential client). Requires --oauth-client-secret.",
    default=None,
    envvar="WINDOWS_MCP_OAUTH_CLIENT_ID",
    type=str,
    show_default=False,
)
@click.option(
    "--oauth-client-secret",
    help="OAuth client secret. Requires --oauth-client-id.",
    default=None,
    envvar="WINDOWS_MCP_OAUTH_CLIENT_SECRET",
    type=str,
    show_default=False,
)
@click.option(
    "--stateless-http",
    help="Run the streamable-http transport in stateless mode (no Mcp-Session-Id header). Lets reconnecting clients survive a server restart without re-handshaking, and removes the per-connection state that prevents horizontal scaling. Has no effect on stdio/sse transports.",
    is_flag=True,
    default=False,
    envvar="WINDOWS_MCP_STATELESS_HTTP",
    show_default=True,
)
@click.option("--profile", type=click.Choice(["full", "ui-core", "browser"]), default="full", show_default=True,
              help="Fixed tool preset; --tools/--exclude-tools can only narrow it.")
@click.option("--static-manifest", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Validate fixed initialize/tools-list against a generated manifest before stdio startup.")
@click.option("--interaction-mode", type=click.Choice(["semantic", "pointer"]), default="semantic",
              envvar="WINDOWS_MCP_INTERACTION_MODE", show_default=True,
              help="Automatic Execute clicks: semantic patterns first, or visible pointer input. Explicit action modes win.")
@click.option("--pointer-motion", type=click.Choice(["natural", "legacy"]), default="natural",
              envvar="WINDOWS_MCP_POINTER_MOTION", show_default=True,
              help="Feedback-controlled adaptive pointer movement, or the previous fixed timing.")
def serve(
    ctx,
    transport,
    host,
    port,
    debug,
    config,
    auth_key,
    allow_insecure_remote,
    ip_allowlist,
    tools,
    exclude_tools,
    cors_origins,
    ssl_certfile,
    ssl_keyfile,
    oauth_client_id,
    oauth_client_secret,
    stateless_http,
    profile,
    static_manifest,
    interaction_mode,
    pointer_motion,
):
    # Windows' default Proactor loop supports Playwright's asyncio subprocess pipes.
    if transport == Transport.STDIO.value:
        os.environ.setdefault("NO_COLOR", "1")
    if debug:
        _enable_debug_logging()

    # Load config file and merge with CLI flags (CLI wins)
    config_path = discover_config_path(config)
    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))

    pick = partial(_choose_value, ctx)
    interaction_mode = pick("interaction_mode", interaction_mode, cfg.interaction.mode, "semantic")
    pointer_motion = pick("pointer_motion", pointer_motion, cfg.interaction.pointer_motion, "natural")
    _configure_runtime(cfg, interaction_mode, pointer_motion)

    transport = pick("transport", transport, cfg.server.transport, "stdio")
    host = pick("host", host, cfg.server.host, "localhost")
    port = int(pick("port", port, cfg.server.port, 8000))
    auth_key = pick("auth_key", auth_key, cfg.server.auth_key, None)
    stateless_http = bool(pick("stateless_http", stateless_http, cfg.server.stateless_http, False))
    allow_insecure_remote = bool(
        pick("allow_insecure_remote", allow_insecure_remote, cfg.server.allow_insecure_remote, False)
    )
    ssl_certfile = pick("ssl_certfile", ssl_certfile, cfg.server.ssl_certfile, None)
    ssl_keyfile = pick("ssl_keyfile", ssl_keyfile, cfg.server.ssl_keyfile, None)
    oauth_client_id = pick("oauth_client_id", oauth_client_id, cfg.security.oauth_client_id, None)
    oauth_client_secret = pick("oauth_client_secret", oauth_client_secret, cfg.security.oauth_client_secret, None)

    cli_tools, cli_exclude = _resolve_tool_selection(
        ctx, cfg, transport=transport, tools=tools, exclude_tools=exclude_tools,
        profile=profile, static_manifest=static_manifest,
    )
    cli_allowlist = (
        [e.strip() for e in ip_allowlist.split(",")]
        if ip_allowlist and _param_explicit(ctx, "ip_allowlist")
        else cfg.security.ip_allowlist
    )
    cli_cors = (
        [o.strip() for o in cors_origins.split(",") if o.strip()]
        if cors_origins and _param_explicit(ctx, "cors_origins")
        else list(cfg.security.cors_origins)
    )

    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.ClickException("--ssl-certfile and --ssl-keyfile must be provided together.")

    if bool(oauth_client_id) != bool(oauth_client_secret):
        raise click.ClickException(
            "OAuth requires both --oauth-client-id and --oauth-client-secret."
        )

    parsed_allowlist = None
    if cli_allowlist:
        try:
            parsed_allowlist = parse_ip_allowlist(cli_allowlist)
        except ValueError as exc:
            raise click.ClickException(f"Invalid ip_allowlist: {exc}")

    configured_oauth = bool(oauth_client_id and oauth_client_secret)
    uses_http = transport != Transport.STDIO.value

    if (
        uses_http
        and not is_loopback_host(host)
        and not auth_key
        and not configured_oauth
        and not allow_insecure_remote
    ):
        raise click.ClickException(
            f"Refusing to bind HTTP transport to '{host}' without authentication.\n"
            "  Use --auth-key <token> or --oauth-client-id/--oauth-client-secret.\n"
            "  Or pass --allow-insecure-remote to explicitly allow unauthenticated access (not recommended)."
        )

    if (auth_key or cli_allowlist) and transport == Transport.STDIO.value:
        logger.warning("--auth-key / --ip-allowlist have no effect on stdio transport")

    computed_allowed_hosts = _allowed_hosts(transport, host, allow_insecure_remote)

    # Set up OAuth routes if configured (HTTP transports only)
    oauth_validator = None
    if configured_oauth and uses_http:
        oauth_validator = _install_oauth(
            oauth_client_id, oauth_client_secret,
            host=host, port=port, tls=bool(ssl_certfile and ssl_keyfile),
        )

    if uses_http and not is_loopback_host(host):
        if not (auth_key or configured_oauth) or not (ssl_certfile and ssl_keyfile) or not parsed_allowlist:
            raise click.ClickException("Remote desktop control requires authentication, TLS and an explicit IP allowlist")

    scheme = "https" if ssl_certfile else "http"
    logger.debug(
        "Starting windows-mcp (transport=%s, %s, auth=%s, oauth=%s, ip-allowlist=%s, cors=%s, tools=%s, exclude=%s)",
        transport,
        scheme,
        "on" if auth_key else "off",
        "on" if configured_oauth else "off",
        cli_allowlist or "off",
        cli_cors or "off",
        cli_tools or "all",
        cli_exclude or "none",
    )
    try:
        _run_server(
            transport=transport,
            host=host,
            port=port,
            auth_key=auth_key,
            ip_allowlist=parsed_allowlist,
            explicit_tools=cli_tools or None,
            exclude_tools=cli_exclude or None,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            oauth_validator=oauth_validator,
            cors_origins=cli_cors or None,
            allowed_hosts=computed_allowed_hosts,
            stateless_http=stateless_http,
        )
        logger.debug("Server shut down normally")
    except Exception:
        logger.error("Server exiting due to unhandled exception", exc_info=True)
        raise


for _command in (auth, connectors_command, doctor_command, install, manifest_command,
                 native_helper_command, approve, network_grants, trust, uninstall):
    main.add_command(_command)


if __name__ == "__main__":
    main()
