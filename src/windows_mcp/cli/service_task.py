"""Install/uninstall the login scheduled task that starts the HTTP server."""

import subprocess
import sys

import click

from windows_mcp.infrastructure import CONFIG_DIR

_TASK_NAME = "windows-mcp-server"
_START_SCRIPT_PATH = CONFIG_DIR / "start-server.cmd"


def _resolve_program() -> list[str]:
    """Return the argv prefix to invoke `windows-mcp serve` from Task Scheduler.

    Uses the running interpreter so the wrapper always targets this exact
    installation, regardless of what (if anything) is on PATH.
    """
    return [sys.executable, "-m", "windows_mcp"]


def _build_start_script(program_args: list[str]) -> str:
    log_out = CONFIG_DIR / "server.log"
    log_err = CONFIG_DIR / "server.error.log"
    command = subprocess.list2cmdline(program_args)
    return f'@echo off\nsetlocal\n{command} 1>>"{log_out}" 2>>"{log_err}"\n'


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _register_task_powershell(task_name: str, script_path: str) -> subprocess.CompletedProcess:
    """Register an ONLOGON scheduled task via PowerShell at RunLevel Limited.

    Unlike `schtasks /Create /SC ONLOGON`, Register-ScheduledTask with
    -RunLevel Limited does not require an elevated shell.
    """
    ps = (
        f"$action  = New-ScheduledTaskAction -Execute '{script_path}';"
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME;"
        f"$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action"
        f" -Trigger $trigger -Settings $set -RunLevel Limited -Force"
    )
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
    )


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["sse", "streamable-http"]),
    default="streamable-http",
    show_default=True,
    help="Transport for the background server (stdio not supported as a service).",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--port", default=8000, show_default=True, type=int, help="Port to bind.")
@click.option("--force", is_flag=True, help="Reinstall even if already installed.")
def install(transport: str, host: str, port: int, force: bool) -> None:
    """Install windows-mcp as a scheduled task that starts at login."""
    query = _schtasks("/Query", "/TN", _TASK_NAME)
    if query.returncode == 0 and not force:
        click.echo(f"Scheduled task '{_TASK_NAME}' is already installed.")
        click.echo("Use --force to reinstall.")
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    exe = _resolve_program()
    args = exe + ["serve", "--transport", transport, "--host", host, "--port", str(port)]
    _START_SCRIPT_PATH.write_text(_build_start_script(args), encoding="utf-8")

    # Remove any existing task first when forcing a reinstall.
    _schtasks("/Delete", "/TN", _TASK_NAME, "/F")

    result = _register_task_powershell(_TASK_NAME, str(_START_SCRIPT_PATH))
    if result.returncode != 0:
        raise click.ClickException(
            f"Register-ScheduledTask failed:\n{result.stderr.strip() or result.stdout.strip()}"
        )

    run_result = _schtasks("/Run", "/TN", _TASK_NAME)
    if run_result.returncode != 0:
        raise click.ClickException(
            f"schtasks /Run failed:\n{run_result.stderr.strip() or run_result.stdout.strip()}"
        )

    click.echo("Scheduled task installed — server is starting now.")
    click.echo(f"  Task      : {_TASK_NAME}")
    click.echo(f"  Transport : {transport}")
    click.echo(f"  Address   : {host}:{port}")
    click.echo(f"  Logs      : {CONFIG_DIR / 'server.log'}")
    click.echo("\nThe server will restart automatically at every login.")
    click.echo("Run `windows-mcp uninstall` to remove it.")


@click.command()
def uninstall() -> None:
    """Remove the windows-mcp scheduled task and stop the background server."""
    stop_result = _schtasks("/End", "/TN", _TASK_NAME)
    if stop_result.returncode == 0:
        click.echo("Stopped the running server.")

    delete_result = _schtasks("/Delete", "/TN", _TASK_NAME, "/F")
    if delete_result.returncode == 0:
        click.echo(f"Removed scheduled task '{_TASK_NAME}'.")
    else:
        click.echo("No scheduled task found.")

    if _START_SCRIPT_PATH.exists():
        _START_SCRIPT_PATH.unlink()
        click.echo(f"Removed {_START_SCRIPT_PATH}")

    click.echo("windows-mcp will no longer start at login.")
