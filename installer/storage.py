"""Private storage, bounded file reads, cross-process locks and recoverable journals."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from installer.backend import atomic_write
from installer.models import ManagerError
from windows_mcp.infrastructure.local_storage import private_directory, FileLock


def file_hash(path: Path) -> str | None:
    path = check_path(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ManagerError("NOT_A_FILE", "目标不是普通文件")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def check_path(path: Path, root: Path | None = None) -> Path:
    if ".." in path.parts:
        raise ManagerError("UNSAFE_PATH", "路径包含上级目录跳转")
    path = path.absolute()
    if os.name == "nt":
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if path.drive.startswith("\\\\") or any(":" in part or part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved for part in path.parts[1:]):
            raise ManagerError("UNSAFE_PATH", "网络路径、设备名称、数据流或歧义路径不允许用于安装")
    for part in [path, *path.parents]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ManagerError("UNSAFE_PATH", "路径含符号链接或目录重解析点")
    if root is not None and not path.is_relative_to(root.absolute()):
        raise ManagerError("PATH_OUTSIDE_SCOPE", "路径超出允许目录")
    return path


class ProcessLock(FileLock):
    def __enter__(self):
        check_path(self.path)
        try:
            return super().__enter__()
        except BlockingIOError as exc:
            raise ManagerError("INSTALLER_BUSY", "另一个维护操作正在进行，请稍后重试") from exc


class Journal:
    """Persist before-images before mutation, plus hashes of exact writes we own."""

    def __init__(self, data: Path, profile_id: str, operation: str, paths: list[Path]):
        self.id = uuid.uuid4().hex
        self.root = data / "transactions" / self.id
        private_directory(self.root)
        self.record = {"schema": 1, "id": self.id, "profile_id": profile_id, "operation": operation, "time": time.time(), "state": "prepared", "files": []}
        for index, path in enumerate(dict.fromkeys(paths)):
            check_path(path)
            before = file_hash(path)
            if before is not None:
                shutil.copyfile(path, self.root / str(index))
                if file_hash(self.root / str(index)) != before:
                    raise ManagerError("BACKUP_VERIFY_FAILED", "备份校验失败，未开始修改")
            self.record["files"].append({"path": str(path.absolute()), "before": before, "after": before, "slot": str(index)})
        self.save()

    def save(self):
        atomic_write(self.root / "manifest.json", json.dumps(self.record, ensure_ascii=False, indent=2).encode("utf-8"))

    def wrote(self, path: Path, expected_hash: str | None) -> None:
        for row in self.record["files"]:
            if row["path"] == str(path.absolute()):
                row["after"] = expected_hash
                self.record["state"] = "writing"
                self.save()
                return
        raise ManagerError("UNPLANNED_WRITE", "操作试图写入未备份的文件")

    def commit(self) -> None:
        self.record["state"] = "committed"
        self.save()

    @classmethod
    def load(cls, data: Path, transaction_id: str, allowed: set[Path]):
        if len(transaction_id) != 32 or any(ch not in "0123456789abcdef" for ch in transaction_id):
            raise ManagerError("INVALID_TRANSACTION", "交易记录 ID 无效")
        obj = cls.__new__(cls)
        obj.id, obj.root = transaction_id, check_path(data / "transactions" / transaction_id, data / "transactions")
        obj.record = json.loads((obj.root / "manifest.json").read_text(encoding="utf-8"))
        if obj.record["id"] != transaction_id:
            raise ManagerError("INVALID_TRANSACTION", "交易记录身份不一致")
        normalized = {str(p.absolute()) for p in allowed}
        for row in obj.record["files"]:
            if row["path"] not in normalized or not row["slot"].isdigit():
                raise ManagerError("UNSAFE_TRANSACTION", "交易路径或备份路径不在受管范围")
            check_path(Path(row["path"]))
            check_path(obj.root / row["slot"], obj.root)
        return obj

    def rollback(self, *, protected: set[Path] | None = None) -> list[str]:
        conflicts = []
        for row in reversed(self.record["files"]):
            path = check_path(Path(row["path"]))
            actual = file_hash(path)
            if actual == row["before"]:
                continue
            if protected and path in protected:
                conflicts.append(path.name)
                continue
            if actual != row["after"]:
                conflicts.append(path.name)
                continue
            try:
                if row["before"] is None:
                    path.unlink(missing_ok=True)
                else:
                    source = check_path(self.root / row["slot"], self.root)
                    if file_hash(source) != row["before"]:
                        raise ManagerError("BACKUP_CORRUPT", "备份散列不一致")
                    staged = path.with_name(".mcp-restore-" + uuid.uuid4().hex)
                    shutil.copyfile(source, staged)
                    try:
                        os.replace(staged, path)
                    finally:
                        staged.unlink(missing_ok=True)
            except OSError:
                conflicts.append(path.name)
        self.record["state"] = "recovery_required" if conflicts else "rolled_back"
        self.record["conflicts"] = conflicts
        self.save()
        return conflicts
