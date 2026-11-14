"""Compile the real .NET bridge and inspect native resources, without installing."""
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from installer.bundle import create
from scripts.build_compat import build
from windows_mcp.product import release_version


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .NET Framework and PE resources")
def test_actual_compatibility_launcher_has_product_version_icon_and_user_token_manifest(tmp_path):
    import win32api
    import win32con

    source, output, work = (tmp_path / name for name in ("payload", "output", "work"))
    source.mkdir()
    version = release_version()
    (source / "Windows-MCP.exe").write_bytes(b"MZ invalid acceptance fixture, never a real program")
    contents = "_runtime-" + "a" * 20
    (source / contents).mkdir()
    (source / contents / "fixture.txt").write_text("only this project fixture")
    create(source, contents, version)
    report = build(source, output, work)
    executable = output / "Windows-MCP.exe"
    language, codepage = win32api.GetFileVersionInfo(str(executable), r"\VarFileInfo\Translation")[0]
    key = rf"\StringFileInfo\{language:04x}{codepage:04x}"
    assert win32api.GetFileVersionInfo(str(executable), key + r"\ProductName") == "Actilo"
    assert win32api.GetFileVersionInfo(str(executable), key + r"\FileVersion") == version + ".0"
    assert report["product"] == "Actilo" and report["version"] == version
    library = win32api.LoadLibraryEx(str(executable), 0, win32con.LOAD_LIBRARY_AS_DATAFILE)
    try:
        assert win32api.EnumResourceNames(library, win32con.RT_GROUP_ICON)
        manifest = ET.fromstring(win32api.LoadResource(library, 24, 1))  # RT_MANIFEST
        identity = manifest.find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity")
        assert identity.attrib == {"version": version + ".0", "name": "Actilo.LegacyMigration"}
        token = manifest.find(".//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel")
        assert token.get("level") == "asInvoker" and token.get("uiAccess") == "false"
    finally:
        win32api.FreeLibrary(library)
    # Executing the compiled stub proves Windows accepts its real manifest.
    # The deliberately invalid payload cannot start; the stub must report it.
    completed = subprocess.run([str(executable)], capture_output=True, timeout=15)
    assert completed.returncode == 1 and (output / "migration-error.json").is_file()
    assert not list(output.glob("migration-payload-*"))


def test_compatibility_build_rejects_a_different_payload_version(tmp_path):
    source = tmp_path / "payload"
    source.mkdir()
    (source / "Windows-MCP.exe").write_bytes(b"MZ fixture")
    contents = "_runtime-" + "a" * 20
    (source / contents).mkdir()
    (source / contents / "fixture.txt").write_text("fixture")
    create(source, contents, "0.0.1")
    with pytest.raises(ValueError, match="versions differ"):
        build(source, tmp_path / "output", tmp_path / "work")
