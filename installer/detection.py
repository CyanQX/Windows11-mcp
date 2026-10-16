"""Read-only client discovery and independently validated integration state."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import struct
from collections import deque
from pathlib import Path

from installer.backend import Configuration, Installer, Profile, SERVER, mcp_entry, read_file
from installer.models import IntegrationStatus as Status, ManagerError, Presence, TargetState
from installer.storage import check_path, file_hash


def is_desktop_executable(path: Path) -> bool:
    """PE GUI subsystem distinguishes a desktop app from similarly named CLI binaries."""
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return False
            stream.seek(0x3C)
            pe = struct.unpack("<I", stream.read(4))[0]
            if pe > 1024 * 1024:
                return False
            stream.seek(pe)
            if stream.read(4) != b"PE\0\0":
                return False
            stream.seek(pe + 24 + 68)
            return struct.unpack("<H", stream.read(2))[0] == 2
    except (OSError, struct.error):
        return False


LAUNCHERS = {
    "codex": ("codex.exe", "chatgpt.exe"),
    "claude": ("claude.exe", "claude.cmd", "claude.bat"),
    "deepseek": ("deepseek harness.exe", "deepseek-harness.exe", "deepseekharness.exe", "dsh.exe", "dsh.cmd", "dsh.bat"),
    "opencode": ("opencode.exe", "opencode.cmd", "opencode.bat"),
    "gemini": ("gemini.exe", "gemini.cmd", "gemini.bat"),
}


def find_client_in_folder(folder: Path, profile_id: str) -> Path:
    """Bounded read-only scan; never run a candidate or follow reparse points."""
    root = check_path(folder)
    if not root.is_dir() or root == Path(root.anchor):
        raise ManagerError("INVALID_CLIENT_FOLDER", "请选择该编程端的安装文件夹")
    pending, matches, count = deque([(root, 0)]), [], 0
    excluded = {"node_modules", ".git", "cache", "caches", "logs", "data", "temp", "tmp"}
    while pending:
        directory, depth = pending.popleft()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > 12000:
                        raise ManagerError("CLIENT_SCAN_LIMIT", "文件夹内容较多，请选择更接近程序所在位置的文件夹")
                    path = Path(entry.path)
                    if entry.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                        continue
                    if entry.is_file(follow_symlinks=False) and entry.name.lower() in LAUNCHERS[profile_id]:
                        check_path(path, root)
                        if profile_id != "codex" or is_desktop_executable(path):
                            matches.append((depth, LAUNCHERS[profile_id].index(entry.name.lower()), path))
                    elif depth < 4 and entry.is_dir(follow_symlinks=False) and entry.name.lower() not in excluded:
                        pending.append((path, depth + 1))
        except PermissionError:
            continue
    if not matches:
        raise ManagerError("CLIENT_NOT_IN_FOLDER", "此文件夹中未找到对应编程端，请重新选择安装文件夹")
    matches.sort(key=lambda item: (item[0], item[1], str(item[2]).casefold()))
    best = [item for item in matches if item[:2] == matches[0][:2]]
    if len(best) > 1:
        raise ManagerError("CLIENT_FOLDER_AMBIGUOUS", "找到多个同名程序，请选择当前使用版本所在的文件夹")
    return matches[0][2]


class ClientDiscovery:
    def __init__(self, home: Path, overrides: dict[str, str] | None = None):
        self.home, self.overrides = home, overrides or {}
        self._desktop_apps = None
        self._registered_harness = None

    def refresh(self):
        self._desktop_apps = None
        self._registered_harness = None

    def registered_harness(self) -> list[Path]:
        """Read both registry views; never execute installer/uninstaller commands."""
        if self._registered_harness is not None:
            return self._registered_harness
        self._registered_harness = []
        if os.name != "nt":
            return self._registered_harness
        import winreg
        branch = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, branch, 0, winreg.KEY_READ | view) as root:
                        for index in range(winreg.QueryInfoKey(root)[0]):
                            try:
                                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                                    def read(name):
                                        try:
                                            value = winreg.QueryValueEx(item, name)[0]
                                            return os.path.expandvars(value.strip()) if isinstance(value, str) else ""
                                        except OSError:
                                            return ""
                                    name = read("DisplayName").lower()
                                    if "deepseek" not in name or "harness" not in name:
                                        continue
                                    location = read("InstallLocation").strip('"')
                                    icon = re.sub(r",\s*-?\d+\s*$", "", read("DisplayIcon")).strip().strip('"')
                                    candidates = ([Path(location) / "DeepSeek Harness.exe"] if location else [])
                                    if icon and Path(icon).stem.lower() in {"deepseek harness", "deepseek-harness", "dsh"}:
                                        candidates.append(Path(icon))
                                    self._registered_harness.extend(path for path in candidates if path not in self._registered_harness)
                            except OSError:
                                continue  # A concurrently removed installation is not a scan failure.
                except OSError:
                    continue
        return self._registered_harness

    def desktop_apps(self) -> dict[str, str]:
        if self._desktop_apps is None:
            self._desktop_apps = {}
            if os.name == "nt":
                # Query installed desktop packages, never launch or require the Codex CLI.
                command = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); Get-AppxPackage | Where-Object { $_.Name -match 'OpenAI.Codex|DeepSeek|Claude' } | Select-Object Name,InstallLocation | ConvertTo-Json -Compress"
                try:
                    value = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
                    text = value.stdout.decode("utf-8-sig", errors="replace").strip()
                    rows = json.loads(text) if text else []
                    for row in rows if isinstance(rows, list) else [rows]:
                        if row.get("Name") and row.get("InstallLocation"):
                            self._desktop_apps[row["Name"].lower()] = row["InstallLocation"]
                except (OSError, subprocess.TimeoutExpired, ValueError):
                    pass
        return self._desktop_apps

    def detect(self, profile: Profile) -> tuple[Presence, str | None]:
        override = self.overrides.get(profile.id)
        if override:
            path = check_path(Path(override))
            if path.is_dir():
                try:
                    path = find_client_in_folder(path, profile.id)
                except (ManagerError, OSError):
                    return Presence.PATH_INVALID, str(path)
            valid = path.is_file() and path.suffix.lower() in {".exe", ".cmd", ".bat"}
            if profile.id == "codex":
                valid = valid and path.name.lower() in {"codex.exe", "chatgpt.exe"} and is_desktop_executable(path)
            return (Presence.DETECTED if valid else Presence.PATH_INVALID), str(path)
        if profile.id == "codex":
            for name, location in self.desktop_apps().items():
                if name.startswith("openai.codex") and Path(location).is_dir():
                    return Presence.DETECTED, location
            for name in ("Codex", "ChatGPT"):
                path = self.home / "AppData" / "Local" / "Programs" / name / (name + ".exe")
                if path.is_file() and is_desktop_executable(path):
                    return Presence.DETECTED, str(path)
            return Presence.NOT_FOUND, None
        names = {"claude": ("claude",), "deepseek": ("dsh", "deepseek-harness"), "opencode": ("opencode",), "gemini": ("gemini",)}
        for name in names[profile.id]:
            path = shutil.which(name)
            if path:
                return Presence.DETECTED, path
        if profile.id == "deepseek":
            for path in self.registered_harness():
                if path.is_file() and is_desktop_executable(path):
                    return Presence.DETECTED, str(path)
            for name, path in self.desktop_apps().items():
                if "deepseek" in name and Path(path).is_dir():
                    return Presence.DETECTED, path
            for folder in ("DeepSeek Harness", "deepseek-harness", "DeepSeek"):
                root = self.home / "AppData" / "Local" / "Programs" / folder
                path = root / "DeepSeek Harness.exe"
                if path.is_file() and is_desktop_executable(path):
                    return Presence.DETECTED, str(path)
        return Presence.NOT_FOUND, None


class TargetDetector:
    def __init__(self, backend: Installer, discovery=None, *, current_version: str = ""):
        self.backend = backend
        self.discovery = discovery or ClientDiscovery(backend.home)
        self.current_version = current_version

    def receipt(self, profile: Profile) -> dict | None:
        path = check_path(self.backend.receipt_path(profile), self.backend.data)
        if not path.exists():
            return None
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("profile") != profile.id or receipt.get("config_path") != str(profile.config) or receipt.get("skill_root") != str(profile.skill_root):
            raise ManagerError("RECEIPT_MISMATCH", "收据与当前目标路径不一致")
        if not isinstance(receipt.get("files"), dict):
            raise ManagerError("RECEIPT_INVALID", "收据文件清单无效")
        for relative, item in receipt["files"].items():
            check_path(profile.skill_root / SERVER / relative, profile.skill_root / SERVER)
            if item.get("backup"):
                check_path(Path(item["backup"]), self.backend.data / "backups")
        if receipt.get("config_backup"):
            check_path(Path(receipt["config_backup"]), self.backend.data / "backups")
        return receipt

    def inspect(self, profile_id: str) -> TargetState:
        profile = self.backend.clients[profile_id]
        problems = []
        presence, client_path = Presence.UNKNOWN, None
        skill = entry_present = receipt_valid = False
        runtime = False
        version = None
        status = Status.UNKNOWN
        try:
            presence, client_path = self.discovery.detect(profile)
            check_path(profile.config)
            check_path(profile.skill_root / SERVER)
            check_path(self.backend.executable, self.backend.data)
            skill = (profile.skill_root / SERVER / "SKILL.md").is_file()
            receipt = self.receipt(profile)
            receipt_valid = receipt is not None
            runtime = self.backend.executable.is_file()
            config = Configuration(profile, read_file(profile.config))
            entry = config.get()
            entry_present = entry is not None
            if receipt is None:
                status = Status.EXTERNAL if skill or entry_present else Status.AVAILABLE
            else:
                version = receipt.get("version")
                missing = []
                altered = []
                for relative, record in receipt["files"].items():
                    path = profile.skill_root / SERVER / relative
                    actual = file_hash(path)
                    if actual is None:
                        missing.append(relative)
                    elif actual != record.get("hash"):
                        altered.append(relative)
                if not skill or not entry_present or missing or not receipt.get("mcp") or "SKILL.md" not in receipt["files"]:
                    status = Status.PARTIAL
                    problems.append("INCOMPLETE_INTEGRATION")
                elif altered:
                    status = Status.BROKEN
                    problems.append("MANAGED_FILE_MODIFIED")
                elif entry != receipt.get("entry") or entry != mcp_entry(profile, self.backend.executable):
                    status = Status.BROKEN
                    problems.append("MCP_ENTRY_MODIFIED")
                elif not runtime:
                    status = Status.BROKEN
                    problems.append("RUNTIME_MISSING")
                elif receipt.get("runtime_sha256") and file_hash(self.backend.executable) != receipt["runtime_sha256"]:
                    status = Status.BROKEN
                    problems.append("RUNTIME_HASH_MISMATCH")
                else:
                    status = Status.INSTALLED_MANAGED
                    if not version:
                        problems.append("LEGACY_RECEIPT")
                    elif self.current_version and version != self.current_version:
                        problems.append("VERSION_DIFFERS")
        except ManagerError as exc:
            status, problems = Status.BROKEN, [exc.code]
        except ValueError:
            status, problems = Status.BROKEN, ["CONFIG_OR_RECEIPT_INVALID"]
        except (OSError, KeyError, TypeError):
            status, problems = Status.UNKNOWN, ["SCAN_FAILED"]
        return TargetState(profile.id, "Codex 桌面端" if profile.id == "codex" else profile.name, presence, client_path, status, skill, entry_present, runtime, receipt_valid, profile.config, profile.skill_root / SERVER, version, tuple(problems))

    def inspect_all(self) -> dict[str, TargetState]:
        if hasattr(self.discovery, "refresh"):
            self.discovery.refresh()
        return {key: self.inspect(key) for key in self.backend.clients}
