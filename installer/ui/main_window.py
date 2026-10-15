"""Qt manager: durable dock workspace and one bounded background operation at a time."""
from __future__ import annotations

import json
import queue
import threading
import traceback

from PySide6.QtCore import QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtWidgets import QMainWindow, QWidget, QProgressBar, QMessageBox, QApplication, QPushButton

from installer.diagnostics import EventLog
from installer.health import HealthChecker
from installer.models import SelectionModel, IntegrationStatus, Presence
from installer.operations import InstallerService
from installer.progress import TaskProgress, segment
from installer.ui.log_view import LogView
from installer.ui.multiselect import MultiSelect
from installer.ui.theme import Theme, button, column, label, row, backdrop
from installer.ui.workspace import Workspace


class MainWindow(QMainWindow):
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
        self.update_release = None
        self.task_progress = TaskProgress()
        self.reload_service(initial=True)
        mode = "便携版 · 数据位于旁边的 data" if settings.portable_data else "当前用户安装"
        self.setWindowTitle(f"Windows MCP 安装与维护管理器  v{self.version}" + (" · 便携版" if settings.portable_data else ""))
        self.setWindowIcon(self.theme.icon("shield"))
        available = self.screen().availableGeometry()
        self.resize(min(1160, available.width() - 30), min(840, available.height() - 45))
        self.setMinimumSize(min(980, available.width() - 30), min(650, available.height() - 45))
        root = QWidget()
        self.setCentralWidget(root)
        layout = column(root, 0, 0)
        brand = label("Windows MCP", size=19, bold=True)
        header = row(brand, label(f"v{self.version}  /  {mode}", size=11, role="secondary"), None)
        header.layout().setContentsMargins(24, 16, 24, 16)
        self.approval_button = button("操作审批", self.open_operator, icon="shield")
        from installer.ui.settings_dialog import SettingsHub
        self.settings_button = button("设置", lambda: SettingsHub(self), icon="settings")
        header.layout().addWidget(self.approval_button)
        header.layout().addWidget(self.settings_button)
        layout.addWidget(header)
        self.workspace = Workspace(self)
        layout.addWidget(self.workspace, 1)

        left = QWidget()
        left.setMinimumWidth(285)
        left_body = column(left, 20, 14)
        left_body.addWidget(label("选择编程端，可同时安装多个", role="secondary", wrap=True))
        self.selector = MultiSelect(left, self.selection, self.selection_changed)
        left_body.addWidget(self.selector)
        self.hint = label("正在检查本机编程端…", size=12, role="secondary", wrap=True)
        left_body.addWidget(self.hint)
        left_body.addStretch()
        left_body.addWidget(label("Skill 提供操作说明\nMCP 提供 31 个真实工具", size=12, role="secondary", wrap=True))
        left_body.addWidget(button("查看安装路径", self.show_paths, icon="folder"))
        left_body.addWidget(label("拖动面板标题可换位或浮动\n拖动分隔边可调整大小", size=11, role="secondary", wrap=True))
        self.workspace.add("targets", "安装目标", left)

        right = QWidget()
        right.setMinimumSize(510, 260)
        right_body = column(right, 20, 12)
        self.badges = [label("编程端：检测中", role="secondary"), label("Skill：检测中", role="secondary"), label("MCP：未验证", role="secondary")]
        right_body.addWidget(row(*self.badges, None))
        from installer.ui.dialogs import Targets
        self.action_buttons = [button(text, callback, icon=icon) for text, icon, callback in (("安装状态", "search", lambda: self.scan(details=True)), ("验证 MCP 连接", "link", lambda: Targets(self, "verify")), ("智能修复", "repair", lambda: Targets(self, "repair")))]
        right_body.addWidget(row(*self.action_buttons))
        self.logs = LogView()
        right_body.addWidget(row(label("安装日志", size=14, bold=True), None, button("复制", self.logs.copy_all, icon="copy", compact=True), button("清空", self.logs.clear, icon="delete", compact=True)))
        right_body.addWidget(self.logs, 1)
        self.health_label = label("尚未验证 · 连接检查会启动真实 MCP 会话", size=11, role="secondary", wrap=True)
        right_body.addWidget(self.health_label)
        self.workspace.add("maintenance", "安装与维护", right)

        footer = QWidget()
        footer.setMinimumWidth(510)
        footer_body = column(footer, 20, 10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 10000)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("当前任务完成进度")
        footer_body.addWidget(self.progress)
        self.status_label = label("准备就绪", size=12, role="secondary", wrap=True)
        self.percent_label = label("0%", size=12, bold=True)
        self.install_button = button("安装 Skill + MCP", self.install, primary=True)
        self.uninstall_button = button("卸载 Skill + MCP", lambda: Targets(self, "uninstall"))
        footer_body.addWidget(row(self.status_label, self.percent_label, None, self.uninstall_button, self.install_button))
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
        self.scan_timer.timeout.connect(self.scan)
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
        self.theme.apply()
        self.logs.apply_appearance()
        self.workspace.canvas.setAnimated(not self.theme.appearance["reduce_motion"])
        for widget in QApplication.allWidgets():
            if isinstance(widget, QPushButton) and widget.property("iconName"):
                widget.setIcon(self.theme.icon(widget.property("iconName")))
            widget.update()
        if self.theme.appearance["reduce_motion"] and self.progress_animation.state() == QVariantAnimation.State.Running:
            self.progress_animation.stop()
            self._animate_progress(round(self.task_progress.fraction * 10000))

    def system_theme_changed(self, *_):
        if self.settings.value["theme"] == "system":
            self.apply_appearance()

    def run(self, work, done=None, *, rescan=True, title="正在执行维护任务"):
        if self.busy:
            return False
        self.busy = True
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
            elif kind == "progress":
                self.task_progress.advance(*value)
                progress_changed = True
            elif kind in {"done", "error"}:
                self.busy = False
                if kind == "error":
                    self.task_progress.complete(False)
                    self.error("操作未完成", value)
                else:
                    result, states, callback = value
                    failed = result is False or isinstance(result, list) and any(isinstance(item, dict) and item.get("ok") is False for item in result)
                    self.task_progress.complete(not failed)
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

    def scan(self, details=False):
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
        return self.run(work, done, rescan=False, title="正在逐项检查客户端、Skill 和 MCP")

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
        self.health_label.setText(f"最近验证 {latest['checked_at']} · {latest['tool_count']} 个工具 · {latest['code']}" if latest else "尚未验证 · 点击上方按钮执行真实 MCP 连接检查")
        available = sum(s.install_eligible for s in states.values())
        self.hint.setText(f"{available} 个编程端可安装。已接入项请在右侧检查、验证或修复。")

    def selection_changed(self):
        self.selector.refresh()
        self.update_controls()

    def update_controls(self):
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
        self.run(lambda: self.service.preview("install", ids), lambda plan: Preview(self, "确认安装 Skill + MCP", plan, lambda: self.run(lambda: self.service.install_targets(ids, digest=plan["digest"]), title="正在安装 Skill 和 MCP")), rescan=False, title="正在核对安装预览")

    def show_paths(self):
        from installer.ui.settings_dialog import paths
        paths(self)

    def open_operator(self, request_id=None):
        if not self.approval_store:
            return
        from installer.ui.operator_dialog import OperatorCenter
        if self.operator_window and self.operator_window.isVisible():
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
                self.approval_button.setText(f"操作审批 ({len(pending)})" if pending else "操作审批")
        except (OSError, ValueError, KeyError):
            pass

    def error(self, title, message):
        from installer.ui.dialogs import Preview
        return Preview(self, title, {"问题": message, "下一步": "在设置 → 日志与诊断查看详情或预览错误报告。"})

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
