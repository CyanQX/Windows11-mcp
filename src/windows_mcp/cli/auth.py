"""``auth``: generate an auth key (and optionally TLS certificates) into config.toml."""

import secrets
import subprocess
import sys

import click

from windows_mcp.infrastructure import CONFIG_DIR, CONFIG_FILE, WindowsMCPConfig, load_config, write_config


def _echo_section(title: str) -> None:
    """Print a section heading via click.echo, falling back to ASCII
    when the current stdout encoding can't represent the U+2500
    box-drawing character.

    Without this guard, `windows-mcp auth` crashes on the default
    PowerShell / cmd console (cp1252) with a UnicodeEncodeError when
    click.echo hits ─. The config file is already written at this
    point, but the noisy traceback obscures the success and breaks
    scripted callers that treat exit-nonzero as a real failure.
    Setting PYTHONIOENCODING=utf-8 is a workaround; this is the fix.
    """
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "─".encode(enc)
        bar = "─" * 3
    except (UnicodeEncodeError, LookupError):
        bar = "==="
    click.echo(f"\n{bar} {title} {bar}")


def _gen_tls(host: str, cert_path, key_path) -> None:
    """Generate a TLS cert/key pair, preferring mkcert over openssl."""
    from pathlib import Path

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    mkcert = subprocess.run(["where", "mkcert"], capture_output=True).returncode == 0

    if mkcert:
        click.echo("mkcert detected — generating a locally-trusted certificate...")
        install = subprocess.run(["mkcert", "-install"], capture_output=True, text=True)
        if install.returncode != 0:
            raise click.ClickException(f"mkcert -install failed:\n{install.stderr.strip()}")

        sans = [host] if host not in ("0.0.0.0", "") else ["localhost", "127.0.0.1", "::1"]
        result = subprocess.run(
            [
                "mkcert",
                "-cert-file",
                str(cert_path),
                "-key-file",
                str(key_path),
                *sans,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(f"mkcert failed:\n{result.stderr.strip()}")
        click.echo("  Certificate is automatically trusted by Windows.")
    else:
        click.echo("mkcert not found — falling back to openssl (self-signed)...")
        click.echo("  Tip: winget install FiloSottile.mkcert  for auto-trusted certs next time.")
        result = subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                "365",
                "-nodes",
                "-subj",
                f"/CN={host or 'windows-mcp'}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(f"openssl failed:\n{result.stderr.strip()}")
        click.echo("  To make Windows trust this cert, run in an elevated PowerShell:")
        click.echo(
            f'    Import-Certificate -FilePath "{cert_path}" -CertStoreLocation Cert:\\LocalMachine\\Root'
        )

    click.echo(f"  cert → {cert_path}")
    click.echo(f"  key  → {key_path}")


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="sse",
    show_default=True,
    help="Transport mode to configure. Saves the choice to config.toml.",
)
@click.option(
    "--host",
    default="0.0.0.0",
    show_default=True,
    help="Host to bind the server to.",
)
@click.option(
    "--port",
    default=8000,
    show_default=True,
    type=int,
    help="Port to bind the server to.",
)
@click.option("--with-tls", is_flag=True, help="Generate a self-signed TLS certificate and key.")
@click.option("--force", is_flag=True, help="Overwrite existing credentials without prompting.")
def auth(transport: str, host: str, port: int, with_tls: bool, force: bool) -> None:
    """Generate an auth key (and optionally TLS certs) and save to ~/.windows-mcp/config.toml."""
    config_path = CONFIG_FILE

    cfg = load_config(config_path) if config_path.exists() else WindowsMCPConfig()

    if cfg.server.auth_key and not force:
        click.echo(f"Auth key already set in {config_path}. Use --force to regenerate.")
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    new_key = secrets.token_urlsafe(32)
    cfg.server.auth_key = new_key
    cfg.server.transport = transport
    cfg.server.host = host
    cfg.server.port = port
    click.echo(f"Generated auth key: {new_key}")

    if with_tls:
        if transport == "stdio":
            raise click.ClickException("TLS has no effect on stdio transport.")
        cert_path = CONFIG_DIR / "cert.pem"
        key_path = CONFIG_DIR / "key.pem"
        _gen_tls(host, cert_path, key_path)
        cfg.server.ssl_certfile = str(cert_path)
        cfg.server.ssl_keyfile = str(key_path)

    write_config(cfg, config_path)
    click.echo(f"\nSaved to {config_path}")

    if transport == "stdio":
        _echo_section("Claude Desktop config (stdio)")
        click.echo(
            """\
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": ["windows-mcp", "serve"]
    }
  }
}"""
        )
        return

    scheme = "https" if with_tls else "http"
    mcp_url = f"{scheme}://{host}:{port}/mcp/"
    sse_url = f"{scheme}://{host}:{port}/sse"

    _echo_section("Start the server")
    click.echo("  windows-mcp serve")

    if transport == "sse":
        _echo_section("Claude Desktop config (SSE)")
        click.echo(
            f"""\
{{
  "mcpServers": {{
    "windows-mcp": {{
      "type": "sse",
      "url": "{sse_url}",
      "headers": {{ "Authorization": "Bearer {new_key}" }}
    }}
  }}
}}"""
        )
    else:
        _echo_section("Claude Desktop config (Streamable HTTP)")
        click.echo(
            f"""\
{{
  "mcpServers": {{
    "windows-mcp": {{
      "type": "http",
      "url": "{mcp_url}",
      "headers": {{ "Authorization": "Bearer {new_key}" }}
    }}
  }}
}}"""
        )
