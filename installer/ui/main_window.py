"""Qt manager: durable dock workspace and one bounded background operation at a time."""
from __future__ import annotations

import json
import queue
import threading
import traceback

from PySide6.QtCore import QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtWidgets import QMainWindow, QWidget, QMessageBox, QApplication, QPushButton

from installer.diagnostics import EventLog
from installer.health import HealthChecker
from installer.models import SelectionModel, IntegrationStatus, Presence
from installer.operations import InstallerService
from installer.progress import TaskProgress, segment
from installer.ui.log_view import LogView
from installer.ui.multiselect import MultiSelect
from installer.ui.theme import Theme, button, column, label, row, backdrop
from installer.ui.workspace import Workspace
from installer.ui.task_strip import TaskStrip


class MainWindow(QMainWindow):
    release_checked = Signal(object)
    approvals_changed = Signal(int)

    def __init__(self, base, settings, executable, *, service=None, approval_store=None, wake_path=None, auto_scan=True):
        super().__init__()
        self.base, self.settings, self.source_executable = base, settings, executable
        self.manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        self.version = self.manifest["version"]
        self.theme = Theme(settings, base)
        self.theme.apply()
        self.events, self.busy, self.selection = queue.Queue(), False, SelectionModel()
        self.service = service
        self.approval_store, self.wake_path = approval_store, wake_path
        self.operator_window, self.known_requests, self.dialogs = None, set(), []
        self.pending_approvals = 0
        self.update_release = None
        self.install_cancel = None
        self.task_progress = TaskProgress()
        self.reload_service(initial=True)
        mode = "本地预览 · 3D 触感玻璃" if executable.name.lower() == "windows-mcp-preview.exe" else "便携版" if settings.portable_data else ""
        self.setWindowTitle(f"Windows MCP 安装与维护管理器  v{self.version}" + (" · " + mode if mode else ""))
        self.setWindowIcon(self.theme.icon("shield"))
        available = self.screen().availableGeometry()
        self.resize(min(1160, available.width() - 30), min(840, available.height() - 45))
        self.setMinimumSize(min(980, available.width() - 30), min(650, available.height() - 45))
        root = QWidget()
        self.setCentralWidget(root)
        layout = column(root, 0, 0)
        from installer.ui.settings_dialog import About, SettingsHub
        self.settings_button = button("设置", lambda: SettingsHub(self), icon="settings", compact=True)
        self.about_button = button("关于", lambda: About(self), icon="info", compact=True)
        self.top_bar = row(self.settings_button, self.about_button, None)
        self.top_bar.layout().setContentsMargins(12, 6, 12, 2)
        layout.addWidget(self.top_bar)
        self.workspace = Workspace(self)
        layout.addWidget(self.workspace, 1)

        left = QWidget()
        left.setMinimumWidth(285)
        left_body = column(left, 20, 14)
        self.selector = MultiSelect(left, self.selection, self.selection_changed)
        left_body.addWidget(self.selector)
        left_body.addStretch()
        left_body.addWidget(button("查看安装路径", self.show_paths, icon="folder"))
        self.workspace.add("targets", "安装目标", left)

        right = QWidget()
        right.setMinimumSize(510, 260)
        right_body = column(right, 20, 12)
        self.badges = [label("编程端：检测中", role="secondary"), label("Skill：检测中", role="secondary"), label("MCP：未验证", role="secondary")]
        right_body.addWidget(row(*self.badges, None))
        from installer.ui.dialogs import Targets
        self.action_buttons = [button(text, callback, icon=icon) for text, icon, callback in (("安装状态", "search", lambda: self.scan(details=True)), ("验证 MCP 连接", "link", lambda: Targets(self, "verify")), ("智能修复", "repair", lambda: Targets(self, "repair")))]
        right_body.addWidget(row(*self.action_buttons))
        self.task_strip = TaskStrip()
        self.progress = self.task_strip.bar
        self.status_label, self.percent_label = self.task_strip.status, self.task_strip.percent
        right_body.addWidget(self.task_strip)
        self.logs = LogView()
        self.undo_button = button("撤销", self.undo_install, icon="restore", compact=True)
        right_body.addWidget(row(label("安装日志", size=14, bold=True), None, self.undo_button, button("复制", self.logs.copy_all, icon="copy", compact=True), button("清空", self.logs.clear, icon="delete", compact=True)))
        right_body.addWidget(self.logs, 1)
        self.workspace.add("maintenance", "安装与维护", right)

        footer = QWidget()
        footer.setMinimumWidth(510)
        footer_body = column(footer, 20, 10)
        footer_body.setContentsMargins(16, 20, 16, 20)
        self.install_button = button("安装 Skill + MCP", self.install, primary=True)
        self.uninstall_button = button("卸载 Skill + MCP", lambda: Targets(self, "uninstall"))
        footer_row = row(None, self.uninstall_button, self.install_button)
        footer_row.layout().setSpacing(4)
        for control in (self.uninstall_button, self.install_button):
            control.ensurePolished()
        # The stylesheet's compact button metrics must not let a restored dock
        # compress this toolbar below its complete, usable control height.
        footer_row.setMinimumSize(footer_row.sizeHint().width(), 44)
        footer.setMinimumWidth(max(510, footer_row.minimumWidth() + 32))
        footer_body.addWidget(footer_row)
        self.workspace.add("actions", "安装操作", footer)
        self.progress_animation = QVariantAnimation(self)
        self.progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.progress_animation.valueChanged.connect(self._animate_progress)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(30)
        self.operator_timer = QTimer(self)
        self.operator_timer.timeout.connect(self.poll_operator)
        self.operator_timer.start(1500)
        self.scan_timer = QTimer(self)
        self.scan_timer.setSingleShot(True)
        self.scan_timer.timeout.connect(lambda: self.scan(quiet=True))
        if auto_scan:
            self.scan_timer.start(150)
            if service is None:
                QTimer.singleShot(2000, self.check_update_hint)
        self.update_controls()
        QTimer.singleShot(0, lambda: self.workspace.load(settings.value.get("layout", {})))
        QApplication.styleHints().colorSchemeChanged.connect(self.system_theme_changed)

    def showEvent(self, event):
        super().showEvent(event)
        backdrop(self, self.theme.mode == "dark")

    def reload_service(self, initial=False):
        if initial and self.service:
            self.service.log.callback = lambda event: self.events.put(("log", event))
            return
        backend = self.settings.backend(self.base / "skills/windows-mcp", self.source_executable)
        value = self.settings.value
        log = EventLog(backend.data, self.settings.home, self.version, emit=lambda event: self.events.put(("log", event)), retention_days=value["log_retention_days"], max_bytes=value["log_max_mb"] * 1048576)
        self.service = InstallerService(backend, self.manifest, discovery=self.settings.discovery(), log=log, health=HealthChecker(self.manifest, value["health_timeout"]))

    def apply_appearance(self):
        from installer.ui.tactile_button import TactileGlassButton
        self.theme.apply()
        self.logs.apply_appearance()
        # Edge sliding has its own animation. Qt's independent dock animation can
        # reapply stale maximized extents while a panel is being restored.
        self.workspace.canvas.setAnimated(False)
        for widget in QApplication.allWidgets():
            if isinstance(widget, TactileGlassButton):
                widget.refresh_appearance()
            if isinstance(widget, QPushButton) and widget.property("iconName"):
                widget.setIcon(self.theme.icon(widget.property("iconName")))
            widget.update()
        if self.theme.appearance["reduce_motion"] and self.progress_animation.state() == QVariantAnimation.State.Running:
            self.progress_animation.stop()
            self._animate_progress(round(self.task_progress.fraction * 10000))

    def system_theme_changed(self, *_):
        if self.settings.value["theme"] == "system":
            self.apply_appearance()

    def run(self, work, done=None, *, rescan=True, title="正在执行维护任务", show_progress=True):
        if self.busy:
            return False
        self.busy = True
        self.task_strip.begin(show_progress)
        self.task_progress = TaskProgress(message=title)
        self.progress_animation.stop()
        self._animate_progress(0)
        self.status_label.setText(title)
        self.update_controls()
        service = self.service

        def report(fraction, message):
            self.events.put(("progress", (fraction, message)))

        def worker():
            try:
                service.progress = segment(report, 0, 0.9 if rescan else 0.99)
                value = work()
                states = None
                if rescan:
                    report(0.9, "正在核对最终安装状态")
                    service.progress = segment(report, 0.9, 0.99)
                    states = service.inspect_all()
                outcome = ("done", (value, states, done))
            except Exception as exc:
                outcome = ("error", str(exc))
                try:
                    service.log.emit("operation", "操作未完成", level="ERROR", error_code=getattr(exc, "code", type(exc).__name__), detail=str(exc), traceback=traceback.format_exc())
                except OSError:
                    pass  # A full/unavailable log disk must not strand the UI as busy.
            finally:
                service.progress = None
            # Publish completion only after releasing the old progress observer;
            # a callback may immediately start the next task on this same service.
            self.events.put(outcome)
        self.worker = threading.Thread(target=worker, daemon=True, name="manager-operation")
        self.worker.start()
        return True

    def _animate_progress(self, value):
        self.progress.setValue(int(value))
        self.percent_label.setText(f"{100 if int(value) == 10000 else min(99, int(value) // 100)}%")

    def render_progress(self):
        target = round(self.task_progress.fraction * 10000)
        self.progress_animation.stop()
        if self.theme.appearance["reduce_motion"]:
            self._animate_progress(target)
        else:
            self.progress_animation.setStartValue(self.progress.value())
            self.progress_animation.setEndValue(target)
            self.progress_animation.setDuration(260)
            self.progress_animation.start()
        self.status_label.setText(self.task_progress.message)

    def poll(self):
        logs, progress_changed = [], False
        for _ in range(160):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                logs.append(value)
            elif kind == "update_hint":
                self.update_release = value
                self.release_checked.emit(value)
            elif kind == "progress":
                self.task_progress.advance(*value)
                progress_changed = True
            elif kind in {"done", "error"}:
                self.busy = False
                self.install_cancel = None
                if kind == "error":
                    self.task_progress.complete(False)
                    self.task_strip.complete(False)
                    self.error("操作未完成", value)
                else:
                    result, states, callback = value
                    cancelled = isinstance(result, list) and bool(result) and all(isinstance(item, dict) and item.get("cancelled") for item in result)
                    failed = result is False or isinstance(result, list) and any(isinstance(item, dict) and item.get("ok") is False and not item.get("cancelled") for item in result)
                    self.task_progress.complete(not failed)
                    if cancelled:
                        self.task_progress.message = "安装已撤销，已恢复到安装前"
                    self.task_strip.complete(not failed)
                    if states is not None:
                        self.accept_states(states)
                    self.render_status()
                    if callback:
                        try:
                            callback(result)
                        except Exception as exc:
                            self.error("结果页面未能打开", str(exc))
                self.update_controls()
                progress_changed = True
        if logs:
            self.logs.append_rows(logs)
        if progress_changed:
            self.render_progress()

    def scan(self, details=False, *, quiet=False):
        def work():
            if (self.service.backend.data / "cleanup-pending.json").exists():
                try:
                    self.service.process_pending_cleanup()
                except Exception as exc:
                    self.service.log.emit("cleanup", "延迟清理尚未完成，保留运行时", level="WARNING", detail=str(exc))
            states = self.service.inspect_all()
            self.service.log.emit("scan", "安装状态检查完成", level="SUCCESS")
            return states
        def done(states):
            self.accept_states(states)
            if details:
                from installer.ui.dialogs import StatusDialog
                StatusDialog(self)
        return self.run(work, done, rescan=False, title="正在逐项检查客户端、Skill 和 MCP", show_progress=not quiet)

    def check_update_hint(self):
        from installer.updates import UpdateService
        updater = UpdateService(self.service.backend.data, self.version, portable=self.settings.portable_data is not None)
        def check():
            try:
                self.events.put(("update_hint", updater.check()))
            except Exception:
                pass  # Optional hint never interferes with maintenance or reports success.
        threading.Thread(target=check, daemon=True, name="release-hint").start()

    def accept_states(self, states):
        self.selection.refresh(states)
        self.selector.refresh()
        self.render_status()
        self.update_controls()

    def render_status(self):
        states = self.selection.states
        self.badges[0].setText(f"编程端  {sum(s.presence == Presence.DETECTED for s in states.values())} 个已检测")
        self.badges[1].setText(f"Skill  {sum(s.status == IntegrationStatus.INSTALLED_MANAGED for s in states.values())} 个已安装")
        results = list(self.service.health_results.values())
        latest = max(results, key=lambda value: value["checked_at"]) if results else None
        self.badges[2].setText("MCP  最近验证通过" if latest and latest["ok"] else "MCP  验证未通过" if latest else "MCP  尚未验证")

    def selection_changed(self):
        self.selector.refresh()
        self.update_controls()

    def update_controls(self):
        self.undo_button.setEnabled((self.install_cancel is not None and not self.install_cancel.is_set()) if self.busy else self.service.latest_install() is not None)
        self.undo_button.setText("正在撤回…" if self.install_cancel is not None and self.install_cancel.is_set() else "撤销")
        self.install_button.setEnabled(bool(self.selection.selected) and not self.busy)
        self.uninstall_button.setEnabled(not self.busy and any(state.receipt_valid for state in self.selection.states.values()))
        for widget in self.action_buttons:
            widget.setEnabled(not self.busy)
        self.selector.trigger.setEnabled(not self.busy)

    def install(self):
        if self.busy or not self.selection.selected:
            return
        ids = sorted(self.selection.selected)
        from installer.ui.dialogs import Preview
        self.run(lambda: self.service.preview("install", ids), lambda plan: Preview(self, "确认安装 Skill + MCP", plan, lambda: self.start_install(ids, plan["digest"])), rescan=False, title="正在核对安装预览")

    def start_install(self, ids, digest):
        if self.busy:
            return
        cancel = threading.Event()
        self.install_cancel = cancel
        def finished(results):
            from installer.ui.dialogs import Results
            batch_id = next((row.get("batch_id") for row in results if row.get("ok")), None)
            if cancel.is_set() and batch_id:
                # The click can land after worker commit but before its UI event.
                def undo():
                    plan = self.service.preview_undo_install(batch_id)
                    return self.service.undo_install(batch_id, plan["digest"])
                self.run(undo, lambda rows: Results(self, "撤销安装结果", rows), title="正在撤回刚刚完成的安装")
            elif any(not row.get("ok") for row in results):
                Results(self, "安装结果", results)
        self.run(lambda: self.service.install_targets(ids, digest=digest, cancel=cancel), finished, title="正在安装 Skill 和 MCP")

    def undo_install(self):
        if self.busy:
            if self.install_cancel is not None:
                self.install_cancel.set()
                self.status_label.setText("正在停止安装并恢复快照…")
                self.update_controls()
            return
        transaction = self.service.latest_install()
        if transaction is None:
            return
        from installer.ui.dialogs import Preview, Results
        def preview(plan):
            Preview(self, "撤销最近一次安装", plan, lambda: self.run(lambda: self.service.undo_install(transaction, plan["digest"]), lambda rows: Results(self, "撤销安装结果", rows), title="正在恢复安装前的文件和配置"))
        self.run(lambda: self.service.preview_undo_install(transaction), preview, rescan=False, title="正在核对可撤销的安装")

    def show_paths(self):
        from installer.ui.settings_dialog import paths
        paths(self)

    def open_operator(self, request_id=None):
        if not self.approval_store:
            return
        from installer.ui.operator_dialog import OperatorCenter
        if self.operator_window and self.operator_window.isVisible():
            if request_id is None:
                self.operator_window.enable_defaults()
            self.operator_window.request_id = request_id or self.operator_window.request_id
            self.operator_window.refresh()
            self.operator_window.raise_()
            self.operator_window.activateWindow()
        else:
            self.operator_window = OperatorCenter(self, self.approval_store, request_id)

    def poll_operator(self):
        try:
            if self.wake_path and self.wake_path.exists():
                wake = json.loads(self.wake_path.read_text(encoding="utf-8"))
                self.wake_path.unlink()
                if wake.get("request_id"):
                    self.open_operator(wake["request_id"])
                else:
                    self.showNormal()
                    self.raise_()
                    self.activateWindow()
            if self.approval_store:
                pending = {item["request_id"] for item in self.approval_store.pending() if not item["approved"]}
                new = pending - self.known_requests
                if new:
                    self.open_operator(next(iter(new)))
                self.known_requests = pending
                if self.pending_approvals != len(pending):
                    self.pending_approvals = len(pending)
                    self.approvals_changed.emit(self.pending_approvals)
        except (OSError, ValueError, KeyError):
            pass

    def error(self, title, message):
        from installer.ui.dialogs import Preview
        return Preview(self, title, {"问题": message})

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "正在安全处理", "请等待当前维护任务完成，避免中断写入或回滚。")
            event.ignore()
            return
        try:
            self.settings.save({**self.settings.value, "layout": self.workspace.snapshot()})
        except Exception as exc:
            self.error("布局未保存", str(exc))
            event.ignore()
            return
        self.workspace.closing = True
        self.poll_timer.stop()
        self.scan_timer.stop()
        self.operator_timer.stop()
        for dock in self.workspace.docks.values():
            if dock.isFloating():
                dock.close()
        for dialog in list(self.dialogs):
            dialog.close()
        event.accept()
