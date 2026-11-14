"""Grant commands: approve, trust and network (operator-only). Never exposed as MCP tools."""

import click

from windows_mcp.infrastructure import CONFIG_DIR


@click.command()
@click.argument("request_id")
@click.option("--yes", is_flag=True, help="Confirm the displayed request from this trusted local CLI.")
def approve(request_id: str, yes: bool) -> None:
    """Review and grant one exact pending request. This is never an MCP tool."""
    import json
    from windows_mcp.infrastructure.approvals import ApprovalStore

    store = ApprovalStore(CONFIG_DIR / "approvals")
    try:
        request = store.read(request_id)
        click.echo(json.dumps({key: request.get(key) for key in ("session_id", "tool", "risk", "authorization_scope", "network_origins", "scope", "action_hash", "description", "expires_at")}, ensure_ascii=False, indent=2))
        if not yes:
            click.confirm("Approve this one-use action for the displayed session?", abort=True)
        store.approve(request_id)
    except (OSError, ValueError, KeyError, PermissionError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("Approved once. Repeat the exact original tool call before this request expires.")


@click.command()
@click.option("--mode", type=click.Choice(["session", "forever", "automatic"]), default="session", show_default=True, help="Scoped temporary/session grant, or persistent installation-wide automatic execution until revoked.")
@click.option("--session-id", help="Server-issued client session id displayed by Session or a pending request.")
@click.option("--application", multiple=True, help="Exact application identity from a signed pending request (repeatable).")
@click.option("--tool", "tools", multiple=True, help="Allowed R2 tool names (repeatable); defaults to the selected application's tool family.")
@click.option("--action", "actions", multiple=True, help="Optional allowed action names (repeatable).")
@click.option("--request-id", help="Use the session/application scope of a signed pending R2 request.")
@click.option("--revoke", is_flag=True, help="Remove the local full-authorization grant.")
@click.option("--yes", is_flag=True, help="Confirm without an interactive prompt.")
def trust(mode: str, session_id: str | None, application: tuple[str, ...], tools: tuple[str, ...],
          actions: tuple[str, ...], request_id: str | None, revoke: bool, yes: bool) -> None:
    """Grant or revoke the operator trust that skips per-call R2 approval. This is never an MCP tool."""
    from windows_mcp.infrastructure.trust import TrustStore

    store = TrustStore(CONFIG_DIR / "approvals")
    try:
        if revoke:
            if not yes:
                click.confirm("Revoke the local full-authorization grant?", abort=True)
            store.revoke()
            click.echo("Revoked. Every protected action asks for approval again.")
            return
        if mode == "automatic":
            if session_id or application or tools or actions or request_id:
                raise ValueError("Automatic mode covers this installation; do not combine it with legacy scoped options")
            click.echo("Persistent automatic mode: all connected clients/applications and HTTP(S) origins; configured capability switches and file scopes still apply.")
            if not yes:
                click.confirm("Enable until explicitly revoked, including locally enabled high-risk capabilities?", abort=True)
            store.grant_automatic(operator="cli")
            from windows_mcp.infrastructure.approvals import ApprovalStore

            ApprovalStore(store.directory).dismiss_automatic()
            click.echo("Persistent automatic authorization saved. Reconnects/restarts do not expire it. Revoke with trust --revoke.")
            return
        if request_id:
            from windows_mcp.infrastructure.approvals import ApprovalStore

            request = ApprovalStore(store.directory).read(request_id)
            if request.get("risk") != 2 or not (request.get("authorization_scope") or {}).get("application"):
                raise ValueError("Choose a scoped R2 request")
            session_id = request["session_id"]
            from windows_mcp.infrastructure.authorization import scope_applications

            application = tuple(scope_applications(request["authorization_scope"]))
        if not session_id or not application:
            raise ValueError("Use --request-id, or both --session-id and --application; unscoped grants are no longer supported")
        click.echo(f"Session: {session_id}; applications: {', '.join(application)}; tools: {', '.join(tools) or 'application family'}")
        if store.status() is not None and not yes:
            click.confirm("A trust grant is already active; replace it?", abort=True)
        if not yes:
            click.confirm(f"Allow protected mouse/keyboard actions without a per-call prompt ({mode})?", abort=True)
        payload = store.grant(mode, operator="cli", session_id=session_id, applications=list(application),
                              tools=list(tools) or None, actions=list(actions) or None)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if payload["expires_at"] is None:
        click.echo("Trust granted: protected R2 actions run without a prompt until revoked.")
    else:
        minutes = max(1, int((payload["expires_at"] - payload["granted_at"]) / 60))
        click.echo(f"Trust granted: protected R2 actions run without a prompt for {minutes} minutes.")
    click.echo("R3 tools stay disabled unless policy.allow_r3 is enabled in config.toml.")


@click.command("network")
@click.option("--session-id", help="Filter/revoke network grants for this server-issued session id.")
@click.option("--revoke", is_flag=True, help="Revoke scoped network grants (all sessions when no id is supplied).")
@click.option("--yes", is_flag=True, help="Confirm revocation without an interactive prompt.")
def network_grants(session_id: str | None, revoke: bool, yes: bool) -> None:
    """Inspect or revoke origin grants; granting happens through local approval of exact requests."""
    import json
    from windows_mcp.infrastructure.network import NetworkGrants

    store = NetworkGrants(CONFIG_DIR / "approvals")
    if revoke:
        if not yes:
            click.confirm("Revoke the displayed network origin grants?", abort=True)
        store.revoke(session_id)
    click.echo(json.dumps(store.status(session_id), ensure_ascii=False, indent=2))
