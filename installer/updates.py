"""Official release verification and an out-of-process, reversible EXE replacement."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import psutil
import requests
from packaging.version import Version

from installer.backend import atomic_write
from installer.models import ManagerError
from installer.distribution import portable_name, setup_name, verify_portable
from installer.storage import ProcessLock, check_path, file_hash, private_directory

REPOSITORY = "CyanQX/Windows11-mcp"
MAX_SIZE = 600 * 1024 * 1024


def official_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "github.com" or not parsed.path.startswith(f"/{REPOSITORY}/releases/download/") or parsed.query or parsed.fragment:
        raise ManagerError("UPDATE_SOURCE_INVALID", "更新资产必须来自本项目的官方 GitHub Release")
    return url


class UpdateService:
    def __init__(self, data: Path, version: str, session=None, *, portable=False):
        self.data, self.version = data, version
        self.session = session or requests.Session()
        self.portable = portable

    def check(self, progress=None):
        try:
            response = self.session.get(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", timeout=(10, 30), headers={"Accept": "application/vnd.github+json", "User-Agent": "Windows-MCP-Manager/" + self.version})
            response.raise_for_status()
            if progress:
                progress(0.35, "官方版本响应已收到")
            if len(response.content) > 2 * 1024 * 1024:
                raise ValueError("release metadata too large")
            row = response.json()
            version = str(Version(row["tag_name"].removeprefix("v")))
            if row.get("draft") or row.get("prerelease"):
                raise ValueError("not a stable release")
            if progress:
                progress(0.55, "稳定版本信息已核对")
            if Version(version) <= Version(self.version):
                return {"available": False, "current": self.version, "latest": version}
            name = portable_name(version) if self.portable else setup_name(version)
            asset = next((a for a in row["assets"] if a["name"] == name), None)
            kind = "portable" if self.portable else "installer"
            if not asset:
                # Earlier releases published the manager itself as this EXE.
                name = f"Windows-MCP-Setup-{version}.exe"
                asset = next((a for a in row["assets"] if a["name"] == name), None)
                kind = "manager"
            if not asset:
                raise ManagerError("UPDATE_ASSET_MISSING", "新版本尚未提供完整 Windows EXE，请稍后重试")
            official_url(asset["browser_download_url"])
            size = asset.get("size")
            if type(size) is not int or not 1024 < size <= MAX_SIZE:
                raise ValueError("invalid size")
            if progress:
                progress(0.75, "官方安装资产与下载大小已核对")
            digest = asset.get("digest") or ""
            expected = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
            checksum = next((a for a in row["assets"] if a["name"] == "SHA256SUMS.txt"), None)
            if expected is None and checksum:
                url = official_url(checksum["browser_download_url"])
                check = self.session.get(url, timeout=(10, 30))
                check.raise_for_status()
                if len(check.content) > 65536:
                    raise ValueError("checksum manifest too large")
                for line in check.text.splitlines():
                    match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
                    if match and match[2] == name:
                        expected = match[1].lower()
            if not expected or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
                raise ManagerError("UPDATE_CHECKSUM_MISSING", "发布资产缺少 SHA-256 校验，已禁止自动替换")
            if progress:
                progress(1, "官方校验信息已核对")
            return {"available": True, "current": self.version, "latest": version, "kind": kind, "name": name, "size": size, "sha256": expected.lower(), "url": asset["browser_download_url"], "notes": str(row.get("body", ""))[:12000]}
        except ManagerError:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise ManagerError("UPDATE_CHECK_FAILED", "无法读取或校验官方更新信息，当前版本已保留") from exc

    def download(self, release, progress=None):
        official_url(release["url"])
        if not re.fullmatch(r"[a-f0-9]{64}", release["sha256"]):
            raise ManagerError("UPDATE_CHECKSUM_MISSING", "校验值无效")
        root = self.data / "updates" / uuid.uuid4().hex
        private_directory(root)
        partial, target = root / "download.partial", root / "next.exe"
        try:
            with self.session.get(release["url"], timeout=(10, 30), stream=True) as response:
                response.raise_for_status()
                total, digest, deadline = 0, hashlib.sha256(), time.monotonic() + 600
                with partial.open("wb") as stream:
                    for block in response.iter_content(1024 * 1024):
                        if time.monotonic() > deadline:
                            raise ValueError("download deadline exceeded")
                        total += len(block)
                        if total > min(MAX_SIZE, release["size"]):
                            raise ValueError("download exceeded declared size")
                        stream.write(block)
                        digest.update(block)
                        if progress:
                            progress(0.9 * total / release["size"], f"已下载 {total / 1048576:.1f} / {release['size'] / 1048576:.1f} MB")
            with partial.open("rb") as stream:
                signature = stream.read(2)
            expected_signature = b"PK" if release.get("kind") == "portable" else b"MZ"
            if total != release["size"] or digest.hexdigest() != release["sha256"] or signature != expected_signature:
                raise ManagerError("UPDATE_VERIFY_FAILED", "下载大小、程序格式或 SHA-256 不符，未替换当前版本")
            if progress:
                progress(0.95, "下载完成，大小、格式及 SHA-256 已核验")
            if release.get("kind") == "portable":
                verified = verify_portable(partial, release["latest"])
                with zipfile.ZipFile(partial) as archive:
                    with archive.open(verified["payload_entry"]) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                if file_hash(target) != verified["payload_sha256"]:
                    target.unlink(missing_ok=True)
                    raise ValueError("Portable payload hash mismatch")
                release["archive_sha256"], release["sha256"] = release["sha256"], verified["payload_sha256"]
                return target
            os.replace(partial, target)
            return target
        except (requests.RequestException, ValueError, zipfile.BadZipFile) as exc:
            raise ManagerError("UPDATE_DOWNLOAD_FAILED", "下载未完成，当前版本已保留") from exc
        finally:
            partial.unlink(missing_ok=True)

    def prepare(self, staged: Path, release: dict, target: Path, nonce=None):
        if release.get("kind") == "installer":
            raise ManagerError("UPDATE_INSTALLER_REQUIRED", "安装版更新通过已校验的更新安装程序执行")
        staged = check_path(staged, self.data / "updates")
        target = check_path(target)
        if file_hash(staged) != release["sha256"] or not target.is_file() or target == staged:
            raise ManagerError("UPDATE_PLAN_INVALID", "更新计划与已校验文件不一致")
        row = {"schema": 1, "nonce": nonce or uuid.uuid4().hex, "staged": str(staged), "target": str(target), "backup": str(staged.parent / "previous.exe"), "sha256": release["sha256"], "old_sha256": file_hash(target), "parent_pid": os.getpid(), "parent_created": psutil.Process().create_time(), "created": time.time(), "data": str(self.data), "version": release["latest"], "state": "prepared"}
        row["parents"] = [{"pid": row["parent_pid"], "created": row["parent_created"]}]
        try:
            parent = psutil.Process().parent()
            if parent and Path(parent.exe()).resolve() == target.resolve():
                row["parents"].append({"pid": parent.pid, "created": parent.create_time()})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        path = staged.parent / "plan.json"
        atomic_write(path, json.dumps(row, indent=2).encode())
        return path, row["nonce"]

    def launch_installer(self, staged: Path, release: dict, target: Path):
        check_path(staged, self.data / "updates")
        if release.get("kind") != "installer" or file_hash(staged) != release["sha256"]:
            raise ManagerError("UPDATE_VERIFY_FAILED", "安装包校验失败，未启动")
        target = check_path(target)
        if not target.is_file() or target.name.casefold() != "windows-mcp.exe":
            raise ManagerError("UPDATE_TARGET_INVALID", "当前管理器位置无效，未启动更新")
        return subprocess.Popen([str(staged), "/SILENT", "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART", "/WMCPUPDATE=1", f"/DIR={target.parent}", f"/LOG={staged.parent / 'installer.log'}"], **background_args())


def load_plan(path: Path, nonce: str):
    path = check_path(path)
    row = json.loads(path.read_text(encoding="utf-8"))
    if row.get("nonce") != nonce or not re.fullmatch(r"[a-f0-9]{32}", nonce) or not 0 <= time.time() - row["created"] < 3600:
        raise ManagerError("UPDATE_PLAN_INVALID", "更新计划无效或已过期")
    root = check_path(Path(row["data"]) / "updates")
    check_path(path, root)
    if path.parent != Path(row["staged"]).parent or Path(row["backup"]) != path.parent / "previous.exe":
        raise ManagerError("UPDATE_PLAN_INVALID", "更新暂存路径不一致")
    for key in ("staged", "backup", "target"):
        row[key] = str(check_path(Path(row[key])))
    if Path(row["staged"]) != path.parent / "next.exe" or Path(row["target"]).suffix.casefold() != ".exe":
        raise ManagerError("UPDATE_PLAN_INVALID", "更新程序名称或目标类型无效")
    if Path(row["target"]).is_relative_to(root):
        raise ManagerError("UPDATE_PLAN_INVALID", "更新目标不能位于更新缓存中")
    return row


def background_args():
    return {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def start_update(path: Path, nonce: str):
    row = load_plan(path, nonce)
    if file_hash(Path(row["staged"])) != row["sha256"]:
        raise ManagerError("UPDATE_VERIFY_FAILED", "暂存文件发生变化")
    return subprocess.Popen([row["staged"], "--apply-update", str(path), "--update-nonce", nonce], **background_args())


def apply_update(path: Path, nonce: str):
    """Runs in the verified *new* EXE; never overwrites a running parent image."""
    row = load_plan(path, nonce)
    staged, target, backup = (check_path(Path(row[k])) for k in ("staged", "target", "backup"))
    if Path(sys.executable).resolve() != staged.resolve() or file_hash(staged) != row["sha256"]:
        raise ManagerError("UPDATE_HELPER_INVALID", "更新助手身份不一致")
    result_path = path.parent / "result.json"
    with ProcessLock(path.parent / "update.lock"):
        deadline = time.monotonic() + 90
        while True:
            active = False
            for parent in row.get("parents", [{"pid": row["parent_pid"], "created": row["parent_created"]}]):
                try:
                    if psutil.pid_exists(parent["pid"]) and abs(psutil.Process(parent["pid"]).create_time() - parent["created"]) < 0.01:
                        active = True
                except psutil.NoSuchProcess:
                    pass
            if not active:
                break
            if time.monotonic() > deadline:
                raise ManagerError("UPDATE_PARENT_BUSY", "旧程序尚未退出，未替换")
            time.sleep(0.2)
        if file_hash(target) != row["old_sha256"] or backup.exists():
            raise ManagerError("UPDATE_TARGET_CHANGED", "旧程序或回滚位置发生变化，已停止更新")
        os.replace(target, backup)
        try:
            shutil.copyfile(staged, target)
            if file_hash(target) != row["sha256"]:
                raise ManagerError("UPDATE_VERIFY_FAILED", "替换后的文件校验失败")
            child = subprocess.Popen([str(target), "--finish-update", str(path), "--update-nonce", nonce], **background_args())
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline and child.poll() is None and not result_path.exists():
                time.sleep(0.25)
            if not result_path.exists() and child.poll() is None:
                row.update(state="recovery_required", error="新版尚未完成维护；保留新旧程序，未中断写入")
                atomic_write(path, json.dumps(row).encode())
                return False
            if not result_path.exists() or not json.loads(result_path.read_text())["ok"]:
                # A PyInstaller one-file process can have a bootloader child. Wait
                # for its normal exit; never kill it in the middle of a journal.
                if child.poll() is None:
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        row.update(state="recovery_required", error="新版尚未退出；回滚副本已保留")
                        atomic_write(path, json.dumps(row).encode())
                        return False
                raise ManagerError("UPDATE_POSTCHECK_FAILED", "新版启动或服务验证未完成")
            row["state"] = "committed"
        except Exception as exc:
            if file_hash(target) not in {None, row["sha256"]}:
                raise ManagerError("UPDATE_ROLLBACK_CONFLICT", "替换目标又被修改，回滚副本已保留") from exc
            target.unlink(missing_ok=True)
            os.replace(backup, target)
            row.update(state="rolled_back", error=str(exc))
            atomic_write(path, json.dumps(row).encode())
            subprocess.Popen([str(target)], **background_args())
            return False
        atomic_write(path, json.dumps(row).encode())
        return True
