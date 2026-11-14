"""Product identity; protocol, package and on-disk compatibility names are stable.

pyproject.toml is the sole release-version source. Frozen builds carry its
generated package metadata, so runtime never depends on a writable version file.
"""
from importlib.metadata import version

NAME = "Actilo"
PACKAGE = "windows-mcp"
PROTOCOL = "windows-mcp"
EXECUTABLE = "Windows-MCP.exe"  # Stable receipts/client commands from 1.x.
APP_ID = "B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99"
REPOSITORY = "https://github.com/CyanQX/Windows11-mcp"
RELEASES = REPOSITORY + "/releases/latest"


def release_version():
    return version(PACKAGE)


def installer_name(value):
    return f"{NAME}_{value}_setup.exe"
