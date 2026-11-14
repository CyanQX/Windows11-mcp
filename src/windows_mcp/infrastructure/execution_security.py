"""Actual Windows token checks, independent of operator approval or capability flags.

This is a privilege ceiling, not a sandbox for arbitrary hostile native code.
Installation maintenance has no callable elevated execution broker.
"""
from __future__ import annotations

import os


class ExecutionDenied(PermissionError):
    code = "NON_ELEVATED_RUNTIME_REQUIRED"


class InstallationInProgress(PermissionError):
    code = "INSTALLATION_IN_PROGRESS"


def require_runtime_available(directory=None):
    from windows_mcp.infrastructure.config import CONFIG_DIR
    directory = directory or CONFIG_DIR
    marker = directory / "setup-active.json"
    if not marker.exists():
        return
    from windows_mcp.infrastructure.approvals import read_signed_record
    if marker.stat().st_size > 65536:
        raise InstallationInProgress("安装事务标记无效，请重新运行 Actilo 安装器恢复。")
    row = read_signed_record(directory / "approvals", marker)
    if not row:
        raise InstallationInProgress("安装事务无法验证，已暂停 AI 操作；请重新运行安装器恢复。")
    raise InstallationInProgress("Actilo 安装／恢复尚未完成，AI 操作已暂停；请等待安装结束，若安装已退出请重新运行安装器恢复。")


def token_status(pid=None):
    if os.name != "nt":
        raise ExecutionDenied("Actilo AI execution requires a verifiable Windows user token")
    import win32api
    import win32con
    import win32security

    process = win32api.GetCurrentProcess() if pid is None else win32api.OpenProcess(0x1000, False, pid)
    try:
        token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
        try:
            user = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
            integrity = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
            if isinstance(integrity, tuple):
                integrity = integrity[0]
            integrity = int(win32security.ConvertSidToStringSid(integrity).rsplit("-", 1)[1])
            return {"user_sid": user, "integrity": integrity,
                    "elevated": bool(win32security.GetTokenInformation(token, win32security.TokenElevation)),
                    "ui_access": bool(win32security.GetTokenInformation(token, win32security.TokenUIAccess)),
                    "session_id": win32security.GetTokenInformation(token, win32security.TokenSessionId),
                    "privileged_rights": [win32security.LookupPrivilegeName(None, privilege) for privilege, _ in
                        win32security.GetTokenInformation(token, win32security.TokenPrivileges) if
                        win32security.LookupPrivilegeName(None, privilege) in {
                            "SeDebugPrivilege", "SeTcbPrivilege", "SeAssignPrimaryTokenPrivilege", "SeCreateTokenPrivilege",
                            "SeLoadDriverPrivilege", "SeImpersonatePrivilege", "SeTrustedCredManAccessPrivilege",
                            "SeBackupPrivilege", "SeRestorePrivilege", "SeTakeOwnershipPrivilege"}]}
        finally:
            token.Close()
    finally:
        if pid is not None:
            process.Close()


def require_standard(pid=None):
    try:
        state = token_status(pid)
    except ExecutionDenied:
        raise
    except Exception as exc:
        raise ExecutionDenied("无法验证进程权限，已禁用 AI 执行") from exc
    if (state["elevated"] or state["integrity"] > 0x2000 or state["ui_access"]
            or state.get("session_id") == 0 or state.get("privileged_rights")
            or state["user_sid"] in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
        raise ExecutionDenied("AI 仅允许普通用户权限。请关闭以管理员身份启动的客户端和 Actilo，再正常启动；完全访问不会改变此限制。")
    return state


def check_window(hwnd):
    import win32process
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return require_standard(pid)


def runtime_status():
    try:
        state = require_standard()
        require_runtime_available()
        return {"allowed": True, "ceiling": "non-elevated user", "integrity": state["integrity"], "elevated": False}
    except InstallationInProgress as exc:
        return {"allowed": False, "ceiling": "non-elevated user", "code": exc.code, "guidance": str(exc)}
    except ExecutionDenied as exc:
        return {"allowed": False, "ceiling": "non-elevated user", "code": exc.code,
                "guidance": "正常重新启动客户端与 Actilo；一次性批准或完全访问都不能覆盖进程权限上限。"}
