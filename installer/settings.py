"""User-scoped manager preferences; no MCP policy changes or implicit path migration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from installer.backend import Installer, atomic_write, profiles
from installer.detection import ClientDiscovery
from installer.models import ManagerError
from installer.storage import check_path, private_directory

ENV_KEYS = {"CODEX_HOME", "CLAUDE_CONFIG_DIR", "DSH_HOME", "XDG_CONFIG_HOME", "OPENCODE_CONFIG"}


class SettingsService:
    def __init__(self, home: Path, *, env=None):
        self.home = home.absolute()
        self.env = dict(os.environ if env is None else env)
        self.path = self.home / ".windows-mcp" / "manager.json"
        self.defaults = {"schema": 1, "theme": "light", "data_dir": str(Path(self.env.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "WindowsMCP"), "environment_overrides": {}, "client_executables": {}, "health_timeout": 60, "log_retention_days": 30, "log_max_mb": 50}
        self.value = self.load()

    def load(self):
        if not self.path.exists():
            return dict(self.defaults)
        check_path(self.path, self.home)
        try:
            value = {**self.defaults, **json.loads(self.path.read_text(encoding="utf-8"))}
            self.validate(value)
            return value
        except (ValueError, TypeError, OSError) as exc:
            raise ManagerError("SETTINGS_INVALID", "管理器设置无法读取，原文件已保留") from exc

    def scoped_directory(self, path: Path):
        check_path(path)
        if not path.is_absolute() or path == Path(path.anchor):
            raise ManagerError("INVALID_DIRECTORY", "请选择当前用户的有效目录")
        users = Path(self.env.get("SystemDrive", "C:") + "/Users")
        if path.is_relative_to(users) and not path.is_relative_to(self.home):
            raise ManagerError("OTHER_USER_PATH", "不能写入其他用户的目录")
        for name in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)"):
            if self.env.get(name) and path.is_relative_to(Path(self.env[name])):
                raise ManagerError("SYSTEM_DIRECTORY", "不能将系统目录或程序目录作为数据目录")
        if not path.is_relative_to(self.home) and os.name == "nt":
            import win32api
            import win32con
            import win32security
            parent = path
            while not parent.exists():
                parent = parent.parent
            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            try:
                current = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            finally:
                token.Close()
            owner = win32security.GetNamedSecurityInfo(str(parent), win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner()
            if owner != current:
                raise ManagerError("DIRECTORY_OWNER_UNKNOWN", "自定义目录须属于当前用户，请选择自己创建的文件夹")

    def validate(self, value: dict):
        if value.get("theme") not in {"light", "dark", "system"}:
            raise ManagerError("INVALID_THEME", "主题设置无效")
        for name, lower, upper in (("health_timeout", 5, 180), ("log_retention_days", 1, 365), ("log_max_mb", 5, 500)):
            if type(value.get(name)) is not int or not lower <= value[name] <= upper:
                raise ManagerError("INVALID_SETTING", f"{name} 超出允许范围 {lower}～{upper}")
        self.scoped_directory(Path(value["data_dir"]))
        overrides = value.get("environment_overrides", {})
        if not isinstance(overrides, dict) or set(overrides) - ENV_KEYS:
            raise ManagerError("INVALID_ENVIRONMENT", "只支持文档列出的客户端目录变量")
        for name, target in overrides.items():
            if target:
                self.scoped_directory(Path(target).parent if name == "OPENCODE_CONFIG" else Path(target))
        executables = value.get("client_executables", {})
        if not isinstance(executables, dict) or set(executables) - {"codex", "claude", "deepseek", "opencode", "gemini"}:
            raise ManagerError("INVALID_CLIENT", "客户端路径设置无效")
        for target in executables.values():
            if target:
                check_path(Path(target))

    def save(self, value: dict):
        value = {**self.defaults, **value}
        self.validate(value)
        # Changing directories cannot strand old receipts or silently migrate integrations.
        if any(value[key] != self.value.get(key) for key in ("data_dir", "environment_overrides")):
            receipts = Path(self.value["data_dir"]) / "receipts"
            if any(receipts.glob("*.json")):
                raise ManagerError("PATH_HAS_MANAGED_TARGETS", "当前目录仍有受管接入，请先卸载这些接入，再修改目录；备份与日志会保留")
        private_directory(self.path.parent)
        atomic_write(self.path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
        self.value = value

    def reset(self):
        self.save(dict(self.defaults))

    def effective_env(self):
        return {**self.env, **{key: value for key, value in self.value["environment_overrides"].items() if value}}

    def backend(self, skill: Path, executable: Path):
        self.validate(self.value)
        backend = Installer(self.home, Path(self.value["data_dir"]), skill, executable, self.effective_env())
        for profile in backend.clients.values():
            self.scoped_directory(profile.skill_root)
            self.scoped_directory(profile.config.parent)
        return backend

    def discovery(self):
        return ClientDiscovery(self.home, self.value["client_executables"])

    def path_details(self):
        return {p.id: {"skill": str(p.skill_root / "windows-mcp"), "mcp": str(p.config), "custom": bool(self.value["environment_overrides"])} for p in profiles(self.home, self.effective_env())}
