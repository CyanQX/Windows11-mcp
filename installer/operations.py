"""One owned installation engine: guarded entry points, plans, journals and rollback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from installer.backend import Configuration, Installer, SERVER, atomic_write, read_file
from installer.detection import TargetDetector
from installer.diagnostics import EventLog
from installer.health import HealthChecker
from installer.models import IntegrationStatus as Status, ManagerError
from installer.storage import Journal, ProcessLock, check_path, file_hash, private_directory


def plan_hash(plan: dict) -> str:
    value = {key: item for key, item in plan.items() if key != "digest"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class InstallerService:
    def __init__(self, backend: Installer, manifest: dict, *, discovery=None, log=None, health=None):
        self.backend, self.manifest = backend, manifest
        backend.version = manifest["version"]
        self.detector = TargetDetector(backend, discovery, current_version=manifest["version"])
        self.log = log or EventLog(backend.data, backend.home, manifest["version"])
        self.health = health or HealthChecker(manifest)
        self.health_results = {}
        self.progress = None
        self._install_cancel = None
        self._install_batch = None

    def _check_install_cancel(self):
        if self._install_cancel is not None and self._install_cancel.is_set():
            raise ManagerError("INSTALL_CANCELLED", "已请求撤销安装")

    def _rollback_install(self, journal):
        keys = journal.record.get("profiles") or [journal.record["profile_id"]]
        # A configuration edited by another program may still need our runtime.
        configs = {row["path"]: row for row in journal.record["files"]}
        changing = {key for key in keys if file_hash(self.backend.clients[key].config) in {configs[str(self.backend.clients[key].config)]["before"], configs[str(self.backend.clients[key].config)]["after"]}}
        references, unknown = self._references()
        protected = {self.backend.executable} if set(references) - changing or unknown else set()
        return journal.rollback(protected=protected)

    def report(self, fraction, message):
        if self.progress:
            self.progress(fraction, message)

    @contextmanager
    def lock(self):
        private_directory(self.backend.data)
        authority = self.backend.home / ".windows-mcp"
        private_directory(authority)
        # The home lock also protects client configs when two processes have
        # different data-directory preferences. Always acquire in this order.
        with ProcessLock(authority / "maintenance.lock"):
            with ProcessLock(self.backend.data / "locks" / "maintenance.lock"):
                yield

    def paths(self, key: str) -> list[Path]:
        profile = self.backend.clients[key]
        paths = [profile.config, self.backend.receipt_path(profile), self.backend.executable]
        relative = {path.relative_to(self.backend.skill).as_posix() for path in self.backend.skill.rglob("*") if path.is_file()}
        receipt = self.detector.receipt(profile)
        if receipt:
            relative.update(receipt["files"])
        paths.extend(check_path(profile.skill_root / SERVER / name, profile.skill_root / SERVER) for name in relative)
        return paths

    def inspect_all(self):
        if hasattr(self.detector.discovery, "refresh"):
            self.detector.discovery.refresh()
        states = {}
        for index, key in enumerate(self.backend.clients):
            states[key] = self.detector.inspect(key)
            self.report((index + 1) / len(self.backend.clients), f"已检查 {states[key].display_name}")
        return states

    def _guard(self, key: str, operation: str):
        state = self.detector.inspect(key)
        if operation == "install" and not state.install_eligible:
            raise ManagerError("INSTALL_REJECTED", f"{state.display_name}：{state.label}，不能重复或覆盖安装")
        if operation in {"repair", "uninstall", "upgrade"} and not state.receipt_valid:
            raise ManagerError("NOT_MANAGED", "没有有效受管收据，不能自动修改")
        if operation == "adopt" and state.status != Status.EXTERNAL:
            raise ManagerError("ADOPTION_REJECTED", "目标已变化，请重新查看接管预览")
        if state.status == Status.UNKNOWN:
            raise ManagerError("STATE_UNKNOWN", "无法确认状态，未开始修改")
        # Validate all paths and backup pointers before preparing any mutation.
        self.paths(key)
        if operation in {"install", "repair", "adopt"} and self.backend.executable.exists() and file_hash(self.backend.executable) != file_hash(self.backend.source_executable):
            references, unknown = self._references()
            if set(references) - {key} or unknown:
                raise ManagerError("SHARED_UPGRADE_REQUIRED", "共享运行时被其他接入引用，请先在「版本与升级」中统一升级受管接入")
        return state

    def preview(self, operation: str, ids: list[str]) -> dict:
        if not ids or len(ids) != len(set(ids)) or any(key not in self.backend.clients for key in ids):
            raise ManagerError("INVALID_TARGETS", "请选择有效且不重复的目标")
        rows = []
        for key in ids:
            state = self.detector.inspect(key)
            profile = self.backend.clients[key]
            config = Configuration(profile, read_file(profile.config))
            paths = self.paths(key)
            rows.append({"id": key, "name": state.display_name, "status": state.status.value, "receipt_valid": state.receipt_valid, "config": str(profile.config), "skill": str(state.skill_path), "entry": self.log.redactor.clean(config.get()), "fingerprints": {str(path): file_hash(path) for path in paths}})
            self.report(len(rows) / len(ids), f"已核对 {state.display_name} 的预览指纹")
        plan = {"operation": operation, "targets": rows, "runtime": str(self.backend.executable), "runtime_retention": "存在其他或未知引用时保留；仅无引用且可安全处理时清理", "version": self.manifest["version"]}
        plan["digest"] = plan_hash(plan)
        return plan

    def _confirm_plan(self, operation, ids, digest):
        plan = self.preview(operation, ids)
        if not digest or plan["digest"] != digest:
            raise ManagerError("PLAN_CHANGED", "预览后目标内容有变化，请重新预览并确认")
        return plan

    def _probe(self, progress=None):
        async def probe():
            work = self.health.probe(self.backend.executable, progress=progress) if isinstance(self.health, HealthChecker) else self.health.probe(self.backend.executable)
            task = asyncio.create_task(work)
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.1)
                    self._check_install_cancel()
                self._check_install_cancel()
                return await task
            finally:
                if not task.done():
                    task.cancel()
                # Always close our own probe transport before restoring its EXE.
                await asyncio.gather(task, return_exceptions=True)
        return asyncio.run(probe())

    def verify(self, key: str, *, progress=None):
        state = self.detector.inspect(key)
        if not state.receipt_valid or not state.runtime_present or "RUNTIME_HASH_MISMATCH" in state.problem_codes:
            raise ManagerError("UNTRUSTED_RUNTIME", "请先检查或修复受管运行时，未启动外部程序")
        self.log.emit("verify", "正在执行真实 MCP stdio 握手", target_id=key)
        result = self._probe(progress or self.report)
        self.health_results[key] = result.to_dict()
        self.log.emit("verify", result.message, level="SUCCESS" if result.ok else "ERROR", error_code=None if result.ok else result.code, target_id=key, tool_count=result.tool_count, checked_at=result.checked_at, server_version=result.server_version)
        return result

    def upgrade_targets(self):
        references, unknown = self._references()
        managed = [key for key in self.backend.clients if self.detector.inspect(key).receipt_valid]
        if unknown or set(references) - set(managed):
            raise ManagerError("EXTERNAL_RUNTIME_REFERENCE", "共享运行时存在外部或未知引用，请先确认外部配置后再统一升级")
        return managed

    def _upgrade(self, ids):
        if set(ids) != set(self.upgrade_targets()):
            raise ManagerError("SHARED_UPGRADE_REQUIRED", "必须同时升级引用共享运行时的全部受管接入")
        paths = set()
        for key in ids:
            self._guard(key, "upgrade")
            paths.update(self.paths(key))
        journal = Journal(self.backend.data, ids[0], "upgrade", sorted(paths))
        journal.record["profiles"] = list(ids)
        journal.save()
        self.backend.on_write = journal.wrote
        self.backend.emit = lambda phase, message: self.log.emit(phase, message, transaction_id=journal.id)
        self.health_results.clear()
        try:
            self.report(0.15, "升级快照已创建")
            for index, key in enumerate(ids):
                self.backend.install(self.backend.clients[key])
                self.report(0.15 + 0.55 * (index + 1) / len(ids), "受管文件已更新，等待真实连接核验")
            checked = self._probe(lambda fraction, message: self.report(0.7 + 0.2 * fraction, message))
            if not checked.ok:
                raise ManagerError(checked.code, checked.message)
            for key in ids:
                if self.detector.inspect(key).status != Status.INSTALLED_MANAGED:
                    raise ManagerError("POSTCONDITION_FAILED", "升级后的完整性检查失败")
                self.health_results[key] = checked.to_dict()
            journal.commit()
            self.report(1, "共享服务升级已提交")
            self.log.emit("upgrade", "共享运行时及全部受管接入已统一升级，真实连接验证通过", level="SUCCESS", transaction_id=journal.id)
            return [{"id": key, "ok": True, "transaction_id": journal.id} for key in ids]
        except Exception as exc:
            self.health_results.clear()
            conflicts = journal.rollback()
            raise ManagerError("RECOVERY_REQUIRED" if conflicts else "UPGRADE_ROLLED_BACK", str(exc)) from exc
        finally:
            self.backend.on_write = self.backend.emit = None

    def _transaction(self, key: str, operation: str, *, index=0, total=1):
        def progress(fraction, message):
            self.report((index + fraction) / total, message)
        state = self._guard(key, operation)
        progress(0.08, f"{state.display_name}：预检查通过")
        profile = self.backend.clients[key]
        self.log.emit("preflight", f"开始{operation}：{state.display_name}", target_id=key)
        free = shutil.disk_usage(self.backend.data).free
        if free < self.backend.source_executable.stat().st_size * 3 + 10 * 1024 * 1024:
            raise ManagerError("INSUFFICIENT_DISK", "可用空间不足以完成暂存、备份与回滚")
        journal = Journal(self.backend.data, key, operation, self.paths(key))
        batch = self._install_batch
        if batch is not None:
            journal.record["batch_id"] = batch.id
            journal.save()
        progress(0.2, "完整快照已保存并校验")
        self.log.emit("backup", "完整快照已保存并校验", level="SUCCESS", target_id=key, transaction_id=journal.id)
        def wrote(path, expected_hash):
            journal.wrote(path, expected_hash)
            if batch is not None:
                batch.wrote(path, expected_hash)
        self.backend.on_write = wrote
        self.health_results.clear()
        def emit(phase, message):
            self.log.emit(phase, message, target_id=key, transaction_id=journal.id)
            self._check_install_cancel()
        self.backend.emit = emit
        try:
            self._check_install_cancel()
            if operation == "uninstall":
                result = self.backend.uninstall(profile)
                progress(0.7, "受管接入已处理，正在检查共享引用")
                for message in result["retained"]:
                    self.log.emit("uninstall", message, level="WARNING", target_id=key, transaction_id=journal.id)
                self._cleanup_runtime(journal)
            else:
                result = self.backend.install(profile)
                progress(0.7, "文件已写入并校验，等待 MCP 握手")
                checked = self._probe(lambda fraction, message: progress(0.7 + 0.2 * fraction, message))
                progress(0.9, "MCP 握手已返回，正在核对安装结果")
                self.health_results[key] = checked.to_dict()
                self.log.emit("verify", checked.message, level="SUCCESS" if checked.ok else "ERROR", target_id=key, transaction_id=journal.id, error_code=None if checked.ok else checked.code, tool_count=checked.tool_count)
                if not checked.ok:
                    raise ManagerError(checked.code, checked.message)
                installed = self.detector.inspect(key)
                if installed.status != Status.INSTALLED_MANAGED:
                    raise ManagerError("POSTCONDITION_FAILED", "安装后静态完整性检查未通过")
            self._check_install_cancel()
            journal.commit()
            progress(1, "事务已提交")
            self.log.emit("commit", "操作提交完成" if not result.get("retained") else "部分卸载完成，用户修改已保留", level="SUCCESS" if not result.get("retained") else "WARNING", target_id=key, transaction_id=journal.id)
            return {"id": key, "ok": not result.get("retained"), "transaction_id": journal.id, **result}
        except Exception as exc:
            self.health_results.clear()
            conflicts = self._rollback_install(journal) if batch is not None else journal.rollback()
            code = "RECOVERY_REQUIRED" if conflicts else getattr(exc, "code", "OPERATION_FAILED")
            self.log.emit("rollback", "回滚需要人工处理" if conflicts else "操作失败，已恢复本次受管改动", level="ERROR", error_code=code, target_id=key, transaction_id=journal.id, detail=str(exc), conflicts=conflicts)
            raise ManagerError(code, str(exc)) from exc
        finally:
            self.backend.on_write = self.backend.emit = None

    def install_targets(self, ids: list[str], emit=None, *, digest=None, cancel=None):
        if not ids or len(ids) != len(set(ids)):
            raise ManagerError("INVALID_TARGETS", "请选择有效且不重复的目标")
        results = []
        with self.lock():
            if hasattr(self.detector.discovery, "refresh"):
                self.detector.discovery.refresh()
            if digest is not None:
                self._confirm_plan("install", ids, digest)
            # No writes if any requested target is already installed/ineligible.
            for key in ids:
                if key not in self.backend.clients:
                    raise ManagerError("INVALID_TARGETS", "目标不存在")
                self._guard(key, "install")
            batch = Journal(self.backend.data, ids[0], "install_batch", list(dict.fromkeys(path for key in ids for path in self.paths(key))))
            batch.record["profiles"] = list(ids)
            batch.save()
            self._install_cancel, self._install_batch = cancel, batch
            try:
                self._check_install_cancel()
                for index, key in enumerate(ids):
                    try:
                        results.append(self._transaction(key, "install", index=index, total=len(ids)))
                    except ManagerError as exc:
                        results.append({"id": key, "ok": False, "error_code": exc.code, "message": str(exc)})
                    self._check_install_cancel()
                if any(row["ok"] for row in results):
                    batch.commit()
                else:
                    self._rollback_install(batch)
                self._check_install_cancel()
            except Exception as exc:
                self.health_results.clear()
                conflicts = self._rollback_install(batch)
                code = "RECOVERY_REQUIRED" if conflicts else getattr(exc, "code", "INSTALL_ROLLED_BACK")
                self.log.emit("undo", "安装已完整撤回" if not conflicts else "部分内容无法恢复，已保留现场", level="SUCCESS" if not conflicts else "ERROR", error_code=code, transaction_id=batch.id, conflicts=conflicts)
                results = [{"id": key, "ok": False, "cancelled": code == "INSTALL_CANCELLED", "error_code": code, "message": "已撤销，恢复到安装前" if not conflicts else "撤销未完成，请查看配置与恢复", "conflicts": conflicts} for key in ids]
            finally:
                self._install_cancel = self._install_batch = None
            for row in results:
                row["batch_id"] = batch.id
        if emit:
            emit(results)
        return results

    def apply_plan(self, operation: str, ids: list[str], digest: str):
        if operation not in {"uninstall", "repair", "adopt", "upgrade"}:
            raise ManagerError("INVALID_OPERATION", "未知维护操作")
        results = []
        with self.lock():
            self._confirm_plan(operation, ids, digest)
            if operation == "upgrade":
                return self._upgrade(ids)
            for index, key in enumerate(ids):
                try:
                    results.append(self._transaction(key, operation, index=index, total=len(ids)))
                except ManagerError as exc:
                    results.append({"id": key, "ok": False, "error_code": exc.code, "message": str(exc)})
        return results

    def _references(self) -> tuple[list[str], list[str]]:
        references, unknown = [], []
        needle = str(self.backend.executable).replace("\\", "/").casefold()
        for key, profile in self.backend.clients.items():
            try:
                config = Configuration(profile, read_file(profile.config))
                text = json.dumps(config.document, ensure_ascii=False).replace("\\\\", "/").replace("\\", "/").casefold()
                if needle in text:
                    references.append(key)
            except (ValueError, OSError, TypeError):
                unknown.append(key)
        # Receipts at old custom locations may still reference this shared runtime.
        for path in (self.backend.data / "receipts").glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if row.get("mcp") and str(row.get("config_path")) not in {str(p.config) for p in self.backend.clients.values()}:
                    unknown.append(path.stem)
            except (ValueError, OSError):
                unknown.append(path.stem)
        return references, unknown

    def _cleanup_runtime(self, journal):
        references, unknown = self._references()
        if references or unknown:
            self.log.emit("cleanup", "共享运行时仍有引用或存在未知状态，已保留", level="INFO", references=references, unknown=unknown)
            return
        if self.backend.executable.exists():
            journal.wrote(self.backend.executable, None)
            try:
                self.backend.executable.unlink()
                self.log.emit("cleanup", "无已知引用的共享运行时已清理", level="SUCCESS")
            except PermissionError:
                atomic_write(self.backend.data / "cleanup-pending.json", json.dumps({"path": str(self.backend.executable), "hash": file_hash(self.backend.executable)}).encode())
                self.log.emit("cleanup", "运行时正在使用，已登记延迟清理", level="WARNING")

    def backup(self, ids: list[str]):
        snapshots = []
        with self.lock():
            for key in ids:
                journal = Journal(self.backend.data, key, "backup", self.paths(key))
                journal.commit()
                snapshots.append(journal.id)
                self.report(len(snapshots) / len(ids), "备份已创建并提交")
                self.log.emit("backup", "配置与受管理文件快照已创建", level="SUCCESS", target_id=key, transaction_id=journal.id)
        return snapshots

    def backups(self):
        rows = []
        for path in (self.backend.data / "transactions").glob("*/manifest.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                rows.append({name: value.get(name) for name in ("id", "profile_id", "profiles", "operation", "time", "state", "batch_id")})
            except (ValueError, OSError):
                continue
        return sorted(rows, key=lambda item: item.get("time", 0), reverse=True)

    def latest_install(self):
        return next((row["id"] for row in self.backups() if row["operation"] == "install_batch" and row["state"] == "committed"), None)

    def _install_journal(self, transaction_id):
        raw = next((row for row in self.backups() if row["id"] == transaction_id), None)
        if not raw or raw["operation"] != "install_batch" or raw["state"] != "committed":
            raise ManagerError("NO_INSTALL_TO_UNDO", "这次安装已撤销，或没有可撤销的安装记录")
        keys = raw.get("profiles") or [raw["profile_id"]]
        if any(key not in self.backend.clients for key in keys):
            raise ManagerError("INVALID_TARGETS", "安装记录中的目标无效")
        allowed = {path for key in keys for path in self.paths(key)}
        return Journal.load(self.backend.data, transaction_id, allowed), keys

    def preview_undo_install(self, transaction_id):
        journal, keys = self._install_journal(transaction_id)
        files, conflicts = {}, []
        for item in journal.record["files"]:
            path = Path(item["path"])
            actual = file_hash(path)
            files[str(path)] = {"current": actual, "before": item["before"], "after": item["after"]}
            if actual not in {item["before"], item["after"]}:
                conflicts.append(str(path))
            if item["before"] is not None and file_hash(journal.root / item["slot"]) != item["before"]:
                raise ManagerError("BACKUP_CORRUPT", "安装前备份校验失败，未执行撤销")
            if path == self.backend.executable and actual != item["before"]:
                references, unknown = self._references()
                if set(references) - set(keys) or unknown:
                    conflicts.append("共享服务已被其他接入使用")
        plan = {"operation": "undo_install", "id": transaction_id, "profiles": keys, "files": files, "conflicts": conflicts}
        plan["digest"] = plan_hash(plan)
        return plan

    def undo_install(self, transaction_id, digest):
        with self.lock():
            plan = self.preview_undo_install(transaction_id)
            if plan["digest"] != digest:
                raise ManagerError("PLAN_CHANGED", "撤销预览后内容发生变化，请重新预览")
            if plan["conflicts"]:
                raise ManagerError("UNDO_CONFLICT", "安装后内容已变化，未执行撤销；请查看变化的文件")
            journal, keys = self._install_journal(transaction_id)
            self.report(0.3, "已核对安装前快照，正在撤回安装")
            conflicts = self._rollback_install(journal)
            self.health_results.clear()
            self.report(1, "安装已撤回" if not conflicts else "部分文件仍需恢复")
            self.log.emit("undo", "安装已完整撤回" if not conflicts else "文件占用或内容变化，撤销未完成", level="SUCCESS" if not conflicts else "ERROR", transaction_id=transaction_id, conflicts=conflicts)
            return [{"id": key, "ok": not conflicts, "message": "已恢复到安装前" if not conflicts else "撤销未完成，请查看配置与恢复", "conflicts": conflicts} for key in keys]

    def preview_restore(self, transaction_id: str, profile_id: str | None = None):
        raw = next((row for row in self.backups() if row["id"] == transaction_id), None)
        if not raw or raw["profile_id"] not in self.backend.clients:
            raise ManagerError("INVALID_TRANSACTION", "未找到可恢复的目标")
        permitted = raw.get("profiles") or [raw["profile_id"]]
        key = profile_id or raw["profile_id"]
        if key not in permitted or any(item not in self.backend.clients for item in permitted):
            raise ManagerError("INVALID_TARGETS", "该备份不包含此目标")
        allowed = {path for item in permitted for path in self.paths(item)}
        journal = Journal.load(self.backend.data, transaction_id, allowed)
        profile = self.backend.clients[key]
        row = next(item for item in journal.record["files"] if item["path"] == str(profile.config))
        original = (journal.root / row["slot"]).read_bytes() if row["before"] else None
        if row["before"] and hashlib.sha256(original).hexdigest() != row["before"]:
            raise ManagerError("BACKUP_CORRUPT", "备份散列不一致，不能恢复")
        before = Configuration(profile, original).get()
        current = Configuration(profile, read_file(profile.config)).get()
        plan = {"operation": "restore_config", "id": transaction_id, "target": key, "path": str(profile.config), "current_hash": file_hash(profile.config), "current_entry": self.log.redactor.clean(current), "restore_entry": self.log.redactor.clean(before), "scope": "仅恢复 Windows-MCP 配置项，保留其他配置和当前 Skill"}
        plan["digest"] = plan_hash(plan)
        return plan

    def restore(self, transaction_id: str, digest: str, profile_id: str | None = None):
        with self.lock():
            plan = self.preview_restore(transaction_id, profile_id)
            if plan["digest"] != digest:
                raise ManagerError("PLAN_CHANGED", "恢复预览后配置发生变化")
            key = plan["target"]
            profile = self.backend.clients[key]
            raw = next(row for row in self.backups() if row["id"] == transaction_id)
            allowed = {path for item in (raw.get("profiles") or [key]) for path in self.paths(item)}
            old = Journal.load(self.backend.data, transaction_id, allowed)
            row = next(item for item in old.record["files"] if item["path"] == str(profile.config))
            content = (old.root / row["slot"]).read_bytes() if row["before"] else None
            if row["before"] and hashlib.sha256(content).hexdigest() != row["before"]:
                raise ManagerError("BACKUP_CORRUPT", "备份散列不一致")
            current_bytes = read_file(profile.config)
            config = Configuration(profile, current_bytes)
            config.set(Configuration(profile, content).get())
            journal = Journal(self.backend.data, key, "restore", [profile.config])
            encoded = config.encode()
            if read_file(profile.config) != current_bytes:
                raise ManagerError("CONCURRENT_CHANGE", "恢复时配置被其他程序修改")
            journal.wrote(profile.config, hashlib.sha256(encoded).hexdigest())
            atomic_write(profile.config, encoded)
            journal.commit()
            self.health_results.clear()
            self.log.emit("restore", "Windows-MCP 配置项已恢复；请重新检测状态", level="SUCCESS", target_id=key, transaction_id=journal.id)

    def recover(self, transaction_id: str):
        raw = next((row for row in self.backups() if row["id"] == transaction_id), None)
        if not raw or raw["state"] not in {"prepared", "writing", "recovery_required"}:
            raise ManagerError("INVALID_TRANSACTION", "该交易不需要崩溃恢复")
        with self.lock():
            allowed = set()
            for key in self.backend.clients:
                try:
                    allowed.update(self.paths(key))
                except (ManagerError, ValueError, OSError, TypeError):
                    profile = self.backend.clients[key]
                    allowed.update([profile.config, self.backend.receipt_path(profile), self.backend.executable])
                    allowed.update(profile.skill_root / SERVER / p.relative_to(self.backend.skill) for p in self.backend.skill.rglob("*") if p.is_file())
            journal = Journal.load(self.backend.data, transaction_id, allowed)
            conflicts = self._rollback_install(journal) if journal.record["operation"] == "install_batch" else journal.rollback()
            self.log.emit("recovery", "交易已恢复" if not conflicts else "仍有用户修改或占用，需查看恢复详情", level="WARNING" if conflicts else "SUCCESS", transaction_id=transaction_id)
            return conflicts

    def process_pending_cleanup(self):
        path = check_path(self.backend.data / "cleanup-pending.json")
        if not path.is_file():
            return False
        with self.lock():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("path") != str(self.backend.executable) or row.get("hash") != file_hash(self.backend.executable):
                raise ManagerError("CLEANUP_CHANGED", "延迟清理的运行时已变化，保留文件")
            if any(self._references()):
                return False
            try:
                self.backend.executable.unlink(missing_ok=True)
            except PermissionError:
                return False
            path.unlink()
            self.log.emit("cleanup", "已完成无引用运行时的延迟清理", level="SUCCESS")
            return True
