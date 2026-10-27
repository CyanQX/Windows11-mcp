"""Local read-only diagnostics. Installed, callable and end-to-end verified are distinct."""

from __future__ import annotations

import ctypes
import asyncio
import importlib.util
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from windows_mcp.vision import native_bridge


def connectors() -> dict:
    import os

    windows_apps = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps"
    candidates = [windows_apps / "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe/WindowsPackageManagerMCPServer.exe",
                  windows_apps / "WindowsPackageManagerMCPServer.exe"]
    executable = next((path for path in candidates if path.is_file()), None)
    odr = shutil.which("odr")
    return {
        "winget": {"available": executable is not None, "winget_installed": shutil.which("winget") is not None,
            "configuration": {"mcpServers": {"winget-mcp": {"command": str(executable), "args": []}}} if executable else None,
            "tools_documented": ["find", "install"], "permissions": "Independent Microsoft server; Windows-MCP grants do not apply",
            "documentation": "https://learn.microsoft.com/en-us/windows/package-manager/winget/mcp-server-setup"},
        "file_explorer": {"available": None, "odr_installed": bool(odr), "discovery_command": [odr, "mcp", "list"] if odr else None,
            "reason": "Discover actual connector IDs/tools with an ODR-capable host; presence is not inferred from Windows build",
            "documentation": "https://learn.microsoft.com/en-us/windows/ai/mcp/file-connector"},
        "system_settings": {"available": None, "odr_installed": bool(odr),
            "reason": "Preview/system dependent; discover actual tools and permissions through the native host",
            "documentation": "https://learn.microsoft.com/en-us/windows/ai/mcp/overview"}}


async def probe_winget() -> dict:
    """Read initialize/tools-list only. Never installs, searches or changes configuration."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from windows_mcp.infrastructure.catalog import initialize_client_options

    result = connectors()
    entry = result["winget"]
    if not entry["available"]:
        return result
    command = entry["configuration"]["mcpServers"]["winget-mcp"]["command"]
    try:
        async with asyncio.timeout(20):
            async with Client(StdioTransport(command=command, args=[]), timeout=15, **initialize_client_options()) as client:
                entry["actual_tools"] = [tool.name for tool in await client.list_tools()]
                entry["handshake_verified"] = True
    except Exception as exc:
        entry.update(handshake_verified=False, error_type=type(exc).__name__)
    entry["mutation_called"] = False
    return result


def doctor(*, refresh: bool = False) -> dict:
    if refresh:
        native_bridge.capabilities.cache_clear()
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    session = ctypes.c_uint32()
    session_ok = bool(kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)))
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    dpi = user32.GetAwarenessFromDpiAwarenessContext(ctypes.c_void_p(user32.GetThreadDpiAwarenessContext()))
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    desktop = user32.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS, never switches desktops.
    if desktop:
        user32.CloseDesktop(ctypes.c_void_p(desktop))
    native = native_bridge.capabilities()
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "python": platform.python_version(), "process_session": session.value if session_ok else None,
        "input_desktop_readable": bool(desktop), "dpi_awareness": {0: "unaware", 1: "system", 2: "per_monitor"}.get(dpi, "unknown"),
        "screenshot_backends": {name: importlib.util.find_spec(module) is not None for name, module in
                                 (("pillow", "PIL"), ("dxcam", "dxcam"), ("mss", "mss"))},
        "browser_dependency_installed": importlib.util.find_spec("playwright") is not None,
        "native_components": native, "connectors": connectors(),
        "qualification": "Dependency/session probes only; capture/OCR/browser task success requires separate smoke tests",
        "frozen": bool(getattr(sys, "frozen", False))}
