"""Privilege ceilings are enforced regardless of operator grants."""
from pathlib import Path

import pytest

from windows_mcp.infrastructure import execution_security as security


@pytest.mark.parametrize("changed", [{"elevated": True}, {"integrity": 0x3000}, {"ui_access": True}, {"user_sid": "S-1-5-18"}])
def test_privileged_tokens_fail_closed(changed, monkeypatch):
    state = {"elevated": False, "integrity": 0x2000, "ui_access": False, "user_sid": "S-1-5-21-user"}
    state.update(changed)
    monkeypatch.setattr(security, "token_status", lambda pid=None: state)
    with pytest.raises(security.ExecutionDenied):
        security.require_standard()


def test_unverifiable_token_is_not_allowed(monkeypatch):
    monkeypatch.setattr(security, "token_status", lambda pid=None: (_ for _ in ()).throw(OSError("unavailable")))
    with pytest.raises(security.ExecutionDenied):
        security.require_standard()


@pytest.mark.parametrize("command", ["Start-Process cmd -Verb RunAs", "runas.exe /user:Administrator cmd", "schtasks /create /rl highest", "sc.exe create svc", "Elevation:Administrator!new:foo"])
def test_explicit_elevation_requests_are_refused(command):
    from windows_mcp.terminal import reject_elevation
    with pytest.raises(security.ExecutionDenied):
        reject_elevation(command)


def _real_standard():
    state = security.token_status()
    if state["elevated"] or state["integrity"] > 0x2000:
        pytest.skip("Real non-elevated terminal acceptance requires a standard-token Windows session")


@pytest.mark.parametrize("shell,command,code", [("powershell", "Write-Output '普通权限'; [Console]::Error.Write('error'); exit 7", 7), ("cmd", "echo 普通权限 & exit /b 3", 3)])
def test_real_shell_output_and_exit(shell, command, code, tmp_path):
    _real_standard()
    from windows_mcp.terminal import execute
    folder = tmp_path / "中文 目录"
    folder.mkdir()
    result = execute(command, shell=shell, cwd=folder)
    assert result["exit_code"] == code and "普通权限" in result["stdout"]
    assert result["cwd"] == str(folder) and not result["running"]
    if shell == "powershell":
        assert result["stderr"] == "error"


def test_real_timeout_cancellation_bounded_output_and_owner(tmp_path):
    _real_standard()
    from windows_mcp.terminal import execute, Terminals
    import threading
    assert execute("Start-Sleep 10", timeout=0.4)["timed_out"]
    stop = threading.Event()
    stop.set()
    assert execute("Start-Sleep 10", cancelled=stop)["cancelled"]
    result = execute("[Console]::Write('x' * 400000)")
    assert result["output_truncated"] and len(result["stdout"]) == 256 * 1024
    registry = Terminals()
    item = registry.start("client-A", "cmd", Path(tmp_path))
    with pytest.raises(PermissionError):
        registry.get("client-B", item["terminal_id"])
    shell = registry.get("client-A", item["terminal_id"])
    shell.write("echo 普通权限\r\n")
    import time
    deadline = time.monotonic() + 5
    while "普通权限" not in shell.status()["stdout"] and time.monotonic() < deadline:
        time.sleep(.05)
    assert "普通权限" in shell.status()["stdout"]
    assert not registry.close("client-A", item["terminal_id"])["running"]
    failed = execute("Get-Item -LiteralPath 'Z:\\Actilo-nonexistent-file-for-test'")
    assert failed["exit_code"] != 0 and failed["stderr"]


def test_unexpected_elevated_parent_never_starts_a_shell(monkeypatch):
    import windows_mcp.terminal as terminal
    monkeypatch.setattr(security, "token_status", lambda pid=None: {"elevated": True, "integrity": 0x3000, "ui_access": False, "user_sid": "S-1-5-21-test"})
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda *a, **k: pytest.fail("Elevated parent must not create an AI child"))
    with pytest.raises(security.ExecutionDenied):
        terminal.execute("Write-Output should-not-run")


def test_shortcut_arguments_are_resolved_without_shell_elevation(tmp_path):
    import pythoncom
    from win32com.shell import shell
    import subprocess
    import struct
    import sys
    from windows_mcp.infrastructure.app_launch import shortcut_command
    path = tmp_path / "普通快捷方式.lnk"
    args = ["--test", "中文 路径", 'quote"value', "tail\\"]
    pythoncom.CoInitialize()
    try:
        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        link.SetPath(sys.executable)
        link.SetArguments(subprocess.list2cmdline(args))
        link.SetWorkingDirectory(str(tmp_path))
        link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), True)
        del link
    finally:
        pythoncom.CoUninitialize()
    command, cwd = shortcut_command(path)
    assert command[1:] == args and Path(command[0]).resolve() == Path(sys.executable).resolve()
    assert cwd == str(tmp_path)
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 20, struct.unpack_from("<I", data, 20)[0] | 0x2000)
    path.write_bytes(data)
    with pytest.raises(security.ExecutionDenied, match="RunAsUser"):
        shortcut_command(path)
