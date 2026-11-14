"""Project-owned preflight/commit/rollback, invoked only by the user installer.

No whole-directory deletions: every changed file has a before-image and a
fingerprint. Unknown or edited integrations abort before any destructive step.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from packaging.version import Version

from installer.backend import atomic_write
from installer.models import ManagerError, IntegrationStatus
from installer.storage import Journal, check_path, file_hash, private_directory
from windows_mcp.infrastructure.approvals import sign_record, read_signed_record
from windows_mcp.product import APP_ID, EXECUTABLE

UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{APP_ID}}}_is1"


def active_path(service):
    return service.backend.home / ".windows-mcp/setup-active.json"


def active(service):
    path = active_path(service)
    if not path.exists():
        return None
    row = read_signed_record(service.backend.home / ".windows-mcp/approvals", path)
    if row is None:
        raise ManagerError("INVALID_SETUP_PLAN", "安装事务标记无效，原文件已保留")
    return row


def owner_alive(row):
    import psutil
    try:
        process = psutil.Process(row["pid"])
        return process.create_time() == row["created"] and process.exe() == row["executable"]
    except psutil.NoSuchProcess:
        return False


def assert_available(service):
    if getattr(service, "_setup_recovery", False):
        return
    row = active(service)
    if not row:
        return
    import psutil
    if owner_alive(row) and row["pid"] in {os.getpid(), *(proc.pid for proc in psutil.Process().parents())}:
        return
    raise ManagerError("INSTALLER_BUSY" if owner_alive(row) else "SETUP_RECOVERY_REQUIRED",
                       "安装事务尚未结束，请完成或重新运行 Actilo 安装器恢复；备份：" + row["plan"])


def registered_install(*, allow_missing=False):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            values = {}
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, index)
                values[name] = {"value": value, "type": kind}
    except FileNotFoundError:
        return None
    def value(name):
        return values.get(name, {}).get("value", "")
    if not (str(value("DisplayName")).startswith(("Windows-MCP", "Windows MCP", "Actilo"))
            and value("Publisher") == "CyanQX"):
        raise ManagerError("INSTALL_OWNER_UNCONFIRMED", "旧安装记录归属不明确，未清理任何文件")
    location = Path(value("InstallLocation"))
    if not location.is_absolute():
        raise ManagerError("INVALID_PREVIOUS_INSTALL", "旧安装目录不是绝对路径")
    root = check_path(location)
    if root == Path(root.anchor) or (not allow_missing and not root.is_dir()):
        raise ManagerError("INVALID_PREVIOUS_INSTALL", "旧安装目录无效，原记录已保留")
    Version(value("DisplayVersion"))
    return {"root": str(root), "version": value("DisplayVersion"), "registry": values}


def legacy_artifacts(service, registered, target):
    """Identify exact old shortcut targets/uninstall records, never filename alone."""
    if not registered:
        return [], []
    import re
    import winreg
    old = check_path(Path(registered["root"]))
    owned = {str(old / EXECUTABLE).casefold(), str(service.backend.executable).casefold()}
    files, values = [], []
    from installer.bundle import BUNDLE_FILE, load, verify
    record = load(old)
    if record:
        verify(old, executable=False)
        files.extend(old / name for name in record["files"] if name != EXECUTABLE)
        files.append(old / BUNDLE_FILE)
    if old != target:
        files.append(old / EXECUTABLE)
        command = registered.get("registry", {}).get("UninstallString", {}).get("value", "")
        match = re.fullmatch(r'"([^"\r\n]+)"', command)
        if match:
            uninstaller = check_path(Path(match[1]), old)
            if uninstaller.parent == old and re.fullmatch(r"unins[0-9]{3}\.exe", uninstaller.name, re.I):
                files.extend(path for path in (uninstaller, uninstaller.with_suffix(".dat")) if path.is_file())
    home = service.backend.home
    candidates = [home / "Desktop/Windows-MCP.lnk", home / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Windows-MCP/Windows-MCP.lnk",
                  home / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Windows-MCP/Uninstall Windows-MCP.lnk"]
    if any(path.is_file() for path in candidates):
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            for path in candidates:
                if not path.is_file():
                    continue
                destination = shell.CreateShortcut(str(check_path(path))).TargetPath
                if (str(Path(destination).absolute()).casefold() in owned
                        or (Path(destination).parent == old and re.fullmatch(r"unins[0-9]{3}\.exe", Path(destination).name, re.I))):
                    files.append(path)
        finally:
            pythoncom.CoUninitialize()
    keyname = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keyname, access=winreg.KEY_READ) as key:
            for name in ("Windows-MCP", "WindowsMCP", "Windows MCP"):
                try:
                    data, kind = winreg.QueryValueEx(key, name)
                    first = re.match(r'^\s*"([^"]+)"|^\s*(\S+)', str(data))
                    if first and str(Path(first[1] or first[2]).absolute()).casefold() in owned:
                        values.append({"key": keyname, "name": name, "value": data, "type": kind})
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass
    return files, values


def remove_registry(values):
    import winreg
    for item in values:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, item["key"], access=winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            if winreg.QueryValueEx(key, item["name"]) != (item["value"], item["type"]):
                raise ManagerError("REGISTRY_CHANGED", "旧启动项在备份后变化，安装已中止")
            winreg.DeleteValue(key, item["name"])


def restore_registry(values):
    import winreg
    conflicts = []
    for item in values:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, item["key"]) as key:
            try:
                current = winreg.QueryValueEx(key, item["name"])
            except FileNotFoundError:
                current = None
            if current is None:
                winreg.SetValueEx(key, item["name"], 0, item["type"], item["value"])
            elif current != (item["value"], item["type"]):
                conflicts.append("registry:" + item["name"])
    return conflicts


def restore_registration(record):
    """Restore only this exact AppId, and only if its current location is ours."""
    previous = record.get("previous_install")
    if previous is False:  # Explicit isolated fixture: no real registry ownership.
        return []
    current = registered_install(allow_missing=True)
    allowed = {str(Path(record["target"]))}
    if previous:
        allowed.add(str(Path(previous["root"])))
    if current and str(Path(current["root"])) not in allowed:
        return ["registry:InstallLocation changed"]
    import winreg
    if previous:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, access=winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            for name, item in previous["registry"].items():
                winreg.SetValueEx(key, name, 0, item["type"], item["value"])
    elif current:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            if winreg.QueryInfoKey(key)[0]:
                return ["registry:unexpected subkeys retained"]
        winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, winreg.KEY_WOW64_64KEY)
    return []


class SetupJournal(Journal):
    def __init__(self, service, paths):
        self.authority = service.backend.home / ".windows-mcp/approvals"
        private_directory(self.authority)
        super().__init__(service.backend.data / "setup", "installer", "install", paths)

    def save(self):
        atomic_write(self.root / "manifest.json", json.dumps(sign_record(self.authority, self.record), ensure_ascii=False).encode())

    @classmethod
    def open(cls, service, path):
        path = check_path(path, service.backend.data / "setup/transactions")
        if path.name != "manifest.json" or len(path.parent.name) != 32:
            raise ManagerError("INVALID_SETUP_PLAN", "安装事务路径无效")
        obj = cls.__new__(cls)
        obj.authority = service.backend.home / ".windows-mcp/approvals"
        obj.record = read_signed_record(obj.authority, path)
        if not obj.record or obj.record.get("id") != path.parent.name or obj.record.get("profile_id") != "installer":
            raise ManagerError("INVALID_SETUP_PLAN", "安装事务签名无效，未恢复或删除任何文件")
        obj.id, obj.root = path.parent.name, path.parent
        for item in obj.record["files"]:
            check_path(Path(item["path"]))
            if not item["slot"].isdigit():
                raise ManagerError("INVALID_SETUP_PLAN", "备份槽位无效")
        return obj


def stop_owned(executables):
    import psutil
    from windows_mcp.infrastructure.execution_security import token_status
    current = token_status()["user_sid"]
    known = {str(check_path(path)).casefold() for path in executables}
    stopped = []
    for proc in psutil.process_iter(["pid", "exe", "create_time"]):
        if proc.pid == os.getpid() or not proc.info["exe"] or str(Path(proc.info["exe"]).absolute()).casefold() not in known:
            continue
        try:
            if token_status(proc.pid)["user_sid"] != current:
                raise ManagerError("OTHER_USER_PROCESS", "旧程序属于另一个 Windows 用户，请该用户先退出")
            # Only exact owned executable paths, never name matching or a client tree.
            stamp = proc.create_time()
            if stamp != proc.info["create_time"]:
                continue
            proc.terminate()
            proc.wait(8)
            stopped.append({"pid": proc.pid, "created": stamp})
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            raise ManagerError("OWNED_PROCESS_BUSY", "旧程序无法安全停止，安装已中止；请关闭本项目程序后重试") from exc
    return stopped


def prepare(service, target, payload, *, registered=None, stop=stop_owned):
    from installer.bundle import BUNDLE_FILE, load, verify
    target = check_path(target)
    if target == Path(target.anchor):
        raise ManagerError("INVALID_DIRECTORY", "不能安装到磁盘根目录")
    registered = registered_install() if registered is None else registered
    bundle = verify(payload.parent)
    files = {target / name: row["sha256"] for name, row in bundle["files"].items()}
    files[target / BUNDLE_FILE] = file_hash(payload.parent / BUNDLE_FILE)
    previous = active(service)
    if previous and not owner_alive(previous):
        service._setup_recovery = True
        try:
            if rollback(service, Path(previous["plan"])):
                raise ManagerError("RECOVERY_REQUIRED", "上次安装备份与当前文件冲突，未继续安装")
            active_path(service).unlink()
        finally:
            service._setup_recovery = False
    with service.lock():
        managed = [key for key, profile in service.backend.clients.items() if service.detector.receipt(profile) is not None]
        for key in managed:
            state = service.detector.inspect(key)
            if state.status != IntegrationStatus.INSTALLED_MANAGED:
                raise ManagerError("MIGRATION_CONFLICT", "受管接入已被修改，原文件已保留，请先修复：" + state.display_name)
        if managed:
            service.upgrade_targets()  # Unknown shared-runtime references must be reviewed.
        paths = {path for key in managed for path in service.paths(key)} | set(files)
        old_files, old_values = legacy_artifacts(service, registered, target)
        data_bundle = load(service.backend.data) if managed else None
        if data_bundle:
            verify(service.backend.data)
            old_files.extend(service.backend.data / name for name in data_bundle["files"] if name != EXECUTABLE)
            old_files.append(service.backend.data / BUNDLE_FILE)
        old_files = list(dict.fromkeys(old_files))
        paths.update(old_files)
        old = Path(registered["root"]) if registered else None
        if old:
            paths.add(check_path(old / EXECUTABLE))
            if load(old):
                verify(old, executable=False)  # A registered broken core may be repaired.
                paths.add(old / BUNDLE_FILE)
        for path in files:
            if path.exists() and (old != target and path != service.backend.executable):
                raise ManagerError("UNOWNED_DESTINATION_FILE", "目标文件已存在且无本项目安装归属，未覆盖：" + path.name)
        journal = SetupJournal(service, sorted(paths))
        legacy = [key for key in managed if Version(service.detector.inspect(key).detected_version or "0") < Version("1.7.0")]
        journal.record.update(target=str(target), managed=managed, legacy=legacy, previous_install=registered,
                              obsolete_files=[str(path) for path in old_files], obsolete_registry=old_values,
                              version=service.manifest["version"], phase="backed_up", payload_sha256=file_hash(payload))
        journal.save()
        import psutil
        parent = psutil.Process().parent()
        marker = {"pid": parent.pid, "created": parent.create_time(), "executable": parent.exe(), "plan": str(journal.root / "manifest.json")}
        atomic_write(active_path(service), json.dumps(sign_record(journal.authority, marker)).encode())
        try:
            journal.record["stopped"] = stop([service.backend.executable, *([old / EXECUTABLE] if old else [])])
            service.backend.on_write = journal.wrote
            for key in legacy:
                result = service.backend.uninstall(service.backend.clients[key])
                if result["retained"]:
                    raise ManagerError("MIGRATION_CONFLICT", "旧组件清理存在冲突，已回滚")
            remove_registry(old_values)
            rows = {item["path"]: item for item in journal.record["files"]}
            binaries = set(([service.backend.executable] if managed else []) + ([old / EXECUTABLE] if old else []))
            for path in binaries:
                rows[str(path.absolute())]["after"] = None
            journal.save()
            retired = []
            for path in binaries:
                if not path.exists():
                    continue
                if file_hash(path) != rows[str(path.absolute())]["before"]:
                    raise ManagerError("INSTALL_PLAN_CHANGED", "旧程序在备份后发生变化，未继续迁移")
                destination = check_path(journal.root / ("retired-" + uuid.uuid4().hex + ".exe"), journal.root)
                os.replace(path, destination)
                retired.append(destination)
            # Reconnecting clients cannot reopen the old stable path now.
            # Terminate a bounded race with the rename, including its new image path.
            journal.record["stopped"].extend(stop([*binaries, *retired]))
            for path in retired:
                path.unlink(missing_ok=True)
            for path in old_files:
                rows[str(path.absolute())]["after"] = None
            journal.record["state"] = "writing"
            journal.save()
            for path in old_files:
                path.unlink(missing_ok=True)
            # Predict Inno's exact new bytes before it writes; rollback only
            # touches old bytes, these bytes, or a recorded owned mutation.
            for path, fingerprint in files.items():
                rows[str(path.absolute())]["after"] = fingerprint
            journal.record.update(phase="prepared")
            journal.save()
            return journal.root / "manifest.json"
        except Exception:
            restore_registry(old_values)
            if not journal.rollback():
                active_path(service).unlink(missing_ok=True)
            raise
        finally:
            service.backend.on_write = None


def commit(service, path):
    from installer.bundle import verify
    journal = SetupJournal.open(service, path)
    if journal.record.get("phase") != "prepared" or journal.record["version"] != service.manifest["version"]:
        raise ManagerError("INVALID_SETUP_STATE", "安装事务版本或阶段不一致")
    with service.lock():
        target = Path(journal.record["target"])
        verify(target)
        if file_hash(target / EXECUTABLE) != journal.record["payload_sha256"]:
            raise ManagerError("INSTALL_VERIFY_FAILED", "主程序校验失败")
        service.backend.on_write = journal.wrote
        try:
            for item in journal.record["files"]:
                if file_hash(Path(item["path"])) != item["after"]:
                    raise ManagerError("INSTALL_PLAN_CHANGED", "备份后文件发生变化，未覆盖用户修改：" + Path(item["path"]).name)
            for key in journal.record["legacy"]:
                service.backend.install(service.backend.clients[key])
            # One owned transaction updates all remaining clients too; using
            # the backend here keeps the global preflight rollback snapshots.
            for key in set(journal.record["managed"]) - set(journal.record["legacy"]):
                service.backend.install(service.backend.clients[key])
            if journal.record["managed"]:
                result = service._probe()
                if not result.ok:
                    raise ManagerError(result.code, result.message)
            for key in journal.record["managed"]:
                state = service.detector.inspect(key)
                if state.status != IntegrationStatus.INSTALLED_MANAGED or state.detected_version != service.manifest["version"]:
                    raise ManagerError("COMPONENT_SYNC_FAILED", "组件未完成一致性验证：" + key)
            journal.record.update(phase="verified", verified_at=time.time())
            journal.commit()
            active_path(service).unlink(missing_ok=True)
            return {"version": service.manifest["version"], "phase": "committed", "managed": journal.record["managed"], "legacy_cleaned": journal.record["legacy"], "backup": str(journal.root)}
        except Exception:
            conflicts = journal.rollback()
            raise ManagerError("RECOVERY_REQUIRED" if conflicts else "INSTALL_ROLLED_BACK", "安装验证失败；备份保留在 " + str(journal.root))
        finally:
            service.backend.on_write = None


def rollback(service, path):
    with service.lock():
        journal = SetupJournal.open(service, path)
        if journal.record["state"] == "committed":
            return []
        conflicts = journal.rollback()
        conflicts.extend(restore_registry(journal.record.get("obsolete_registry", [])))
        conflicts.extend(restore_registration(journal.record))
        if not conflicts:
            active_path(service).unlink(missing_ok=True)
        return conflicts
