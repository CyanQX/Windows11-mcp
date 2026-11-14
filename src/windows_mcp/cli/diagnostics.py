"""Read-only diagnostics, manifest export and the private native helper entry."""

import asyncio
import sys

import click


@click.command("doctor")
@click.option("--output", type=click.Path(dir_okay=False), default=None)
def doctor_command(output: str | None) -> None:
    """Read-only session, DPI, native dependency and connector diagnostics."""
    from windows_mcp.infrastructure.diagnostics import doctor
    from pathlib import Path
    import json

    result = json.dumps(doctor(refresh=True), ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(result + "\n", encoding="utf-8")
    else:
        click.echo(result)


@click.command("connectors")
@click.option("--output", type=click.Path(dir_okay=False), default=None)
@click.option("--probe", is_flag=True, help="Read the installed WinGet server's handshake/tools-list; no tool mutations.")
def connectors_command(output: str | None, probe: bool) -> None:
    """Inspect optional Microsoft companions; print fragments without changing client settings."""
    from windows_mcp.infrastructure.diagnostics import connectors, probe_winget
    from pathlib import Path
    import json

    result = json.dumps(asyncio.run(probe_winget()) if probe else connectors(), ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(result + "\n", encoding="utf-8")
    else:
        click.echo(result)


@click.command("manifest")
@click.option("--profile", type=click.Choice(["full", "ui-core", "browser"]), default="full")
@click.option("--output", required=True, type=click.Path(dir_okay=False))
def manifest_command(profile: str, output: str) -> None:
    """Generate fixed static responses using the active FastMCP/MCP generation."""
    from windows_mcp.infrastructure.catalog import export_manifest
    from pathlib import Path
    import json

    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3])) / "manifest.json"
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {"manifest_version": "0.4"}
    result = asyncio.run(export_manifest(base, profile))
    # The local CLI explicitly chooses the destination. Manifest fields are JSON
    # data only and must never participate in selecting a filesystem path.
    destination = Path(output).expanduser().resolve()
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    click.echo(f"Generated {profile} manifest: {destination}")


@click.command("native-helper", hidden=True)
@click.option("--stream", is_flag=True, hidden=True)
def native_helper_command(stream: bool) -> None:
    """Private helper protocol used by the native bridge."""
    from windows_mcp.vision.native_worker import main as worker_main

    worker_main(stream=stream)
