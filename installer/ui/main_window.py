"""Compact Fluent manager with all blocking work outside Tk's event thread."""

from __future__ import annotations

import json
import queue
import threading
import traceback
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from installer.diagnostics import EventLog
from installer.health import HealthChecker
from installer.models import SelectionModel, IntegrationStatus, Presence
from installer.operations import InstallerService
from installer.ui.dialogs import Diagnostics, Preview, Targets
from installer.ui.log_view import LogView
from installer.ui.multiselect import MultiSelect
from installer.ui.settings_dialog import SettingsHub, paths
from installer.ui.theme import COLORS, Icons, button, label, panel, fit_window


class MainWindow(ctk.CTk):
    def __init__(self, base: Path, settings, executable: Path, *, service=None, approval_store=None, wake_path=None):
        ctk.set_appearance_mode(settings.value["theme"])
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.base, self.settings, self.source_executable = base, settings, executable
        self.manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        self.version = self.manifest["version"]
        self.title(f"Windows MCP 安装与维护管理器   v{self.version}")
        _, available_height = fit_window(self, 1100, 750, (1000, 680))
        compact = available_height < 680
        self.configure(fg_color=COLORS["background"])
        self.events = queue.Queue()
        self.busy = False
        self.selection = SelectionModel()
        self.icons = Icons(base)
        self.operator_window = None
        self.approval_store, self.wake_path = approval_store, wake_path
        self.known_requests = set()
        self.service = service
        self.reload_service(initial=True)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent", height=62)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(12, 5))
        label(header, "当前用户安装", size=12, color="secondary").pack(side="left", padx=4)
        self.settings_button = button(header, "设置", lambda: SettingsHub(self), icon=self.icons.get("settings", 19), width=100)
        self.settings_button.pack(side="right")
        self.approval_button = button(header, "操作审批", self.open_operator, icon=self.icons.get("shield", 19), width=125)
        self.approval_button.pack(side="right", padx=10)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=22, pady=10)
        content.grid_columnconfigure(0, weight=3, minsize=230)
        content.grid_columnconfigure(1, weight=7, minsize=520)
        content.grid_rowconfigure(0, weight=1)
        left = panel(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        label(left, "安装目标", size=23, bold=True).pack(anchor="w", padx=22, pady=(14, 14) if compact else (22, 22))
        label(left, "选择编程端（可多选）", size=13, bold=True).pack(anchor="w", padx=22, pady=(0, 10))
        self.selector = MultiSelect(left, self.selection, self.icons, self.selection_changed)
        self.selector.pack(fill="x", padx=20)
        self.hint = label(left, "正在检测编程端…", size=12, color="secondary", justify="left", wraplength=255, anchor="nw")
        self.hint.pack(anchor="w", fill="x", padx=22, pady=12)

        right = panel(content)
        right.grid(row=0, column=1, sticky="nsew")
        label(right, "安装与维护", size=23, bold=True).pack(anchor="w", padx=22, pady=(14, 6) if compact else (22, 12))
        badges = ctk.CTkFrame(right, fg_color="transparent")
        badges.pack(fill="x", padx=22)
        self.badges = []
        for text in ("编程端：检测中", "Skill：检测中", "MCP：未验证"):
            badge = label(badges, text, size=12, color="secondary", fg_color=COLORS["card"], corner_radius=8, padx=10, pady=8)
            badge.pack(side="left", padx=(0, 8))
            self.badges.append(badge)
        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.pack(fill="x", padx=22, pady=(8, 8) if compact else (16, 12))
        actions.grid_columnconfigure((0, 1, 2), weight=1, uniform="action")
        self.action_buttons = []
        for index, (text, icon, callback) in enumerate([
            ("检查安装状态", "search", lambda: self.scan(details=True)),
            ("验证 MCP 连接", "link", lambda: self.targets("verify")),
            ("智能修复", "repair", lambda: self.targets("repair")),
        ]):
            control = button(actions, text, callback, icon=self.icons.get(icon, 22), width=120)
            control.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 5, 0 if index == 2 else 5))
            self.action_buttons.append(control)
        log_header = ctk.CTkFrame(right, fg_color="transparent")
        log_header.pack(fill="x", padx=22, pady=(3, 8))
        label(log_header, "安装日志", size=16, bold=True).pack(side="left")
        button(log_header, "清空", lambda: self.logs.clear(), width=65).pack(side="right")
        button(log_header, "复制", lambda: self.logs.copy(), width=65).pack(side="right", padx=7)
        self.logs = LogView(right)
        self.logs.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        self.health_label = label(right, "MCP 尚未验证 · 点击上方按钮执行真实连接检查", size=11, color="secondary", anchor="w", wraplength=625)
        self.health_label.pack(fill="x", padx=22, pady=(0, 14))

        footer = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(footer, height=3, fg_color=COLORS["border"], progress_color=COLORS["accent"])
        self.progress.set(0)
        self.progress.pack(fill="x")
        controls = ctk.CTkFrame(footer, fg_color="transparent")
        controls.pack(fill="x", padx=24, pady=18)
        self.install_button = button(controls, "安装 Skill + MCP", self.install, primary=True, width=210)
        self.install_button.pack(side="right")
        self.uninstall_button = button(controls, "卸载 Skill + MCP", lambda: self.targets("uninstall"), width=180)
        self.uninstall_button.pack(side="right", padx=12)
        self.status_label = label(controls, "准备就绪", size=12, color="secondary", anchor="w", wraplength=520)
        self.status_label.pack(side="left", fill="x", expand=True, padx=(0, 15))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(80, self.poll)
        self.after(300, self.poll_operator)
        self.scan_after_id = self.after(150, self.scan)

    def reload_service(self, initial=False):
        ctk.set_appearance_mode(self.settings.value["theme"])
        if initial and self.service:
            self.service.log.callback = lambda row: self.events.put(("log", row))
            return
        backend = self.settings.backend(self.base / "skills" / "windows-mcp", self.source_executable)
        value = self.settings.value
        log = EventLog(backend.data, self.settings.home, self.version, emit=lambda row: self.events.put(("log", row)), retention_days=value["log_retention_days"], max_bytes=value["log_max_mb"] * 1024 * 1024)
        self.service = InstallerService(backend, self.manifest, discovery=self.settings.discovery(), log=log, health=HealthChecker(self.manifest, value["health_timeout"]))

    def run(self, work, done=None, *, rescan=True):
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前操作完成。", parent=self)
            return
        self.busy = True
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_label.configure(text="正在执行，请查看实时日志…", text_color=COLORS["secondary"])
        self.update_controls()

        def worker():
            try:
                value = work()
                states = self.service.inspect_all() if rescan else None
                self.events.put(("done", (value, states, done)))
            except Exception as exc:
                self.service.log.emit("operation", "操作未完成", level="ERROR", error_code=getattr(exc, "code", type(exc).__name__), detail=str(exc), traceback=traceback.format_exc())
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="manager-operation").start()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.logs.append(value)
                elif kind in {"done", "error"}:
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(0)
                    if kind == "error":
                        self.status_label.configure(text="操作失败 · 可在设置中预览错误报告", text_color=COLORS["error"])
                        messagebox.showerror("操作未完成", value, parent=self)
                    else:
                        result, states, callback = value
                        if states is not None:
                            self.selection.refresh(states)
                            self.selector.refresh()
                            self.render_status()
                        failed = isinstance(result, list) and any(isinstance(row, dict) and row.get("ok") is False for row in result)
                        self.status_label.configure(text="部分目标未完成，请查看日志与诊断" if failed else "操作完成 · 状态已刷新", text_color=COLORS["warning"] if failed else COLORS["secondary"])
                        if callback:
                            callback(result)
                    self.update_controls()
        except queue.Empty:
            pass
        self.after(80, self.poll)

    def scan(self, details=False):
        def work():
            if (self.service.backend.data / "cleanup-pending.json").exists():
                try:
                    self.service.process_pending_cleanup()
                except Exception as exc:
                    self.service.log.emit("cleanup", "延迟清理尚未完成，保留运行时", level="WARNING", detail=str(exc))
            states = self.service.inspect_all()
            self.service.log.emit("scan", f"安装状态扫描完成：{sum(s.presence == Presence.DETECTED for s in states.values())} 个客户端已检测", level="SUCCESS")
            return states
        def done(states):
            self.accept_states(states)
            if details:
                Preview(self, "安装状态详情", {key: value.to_dict() for key, value in states.items()})
        self.run(work, done, rescan=False)

    def accept_states(self, states):
        self.selection.refresh(states)
        self.selector.refresh()
        self.render_status()
        self.update_controls()

    def render_status(self):
        states = self.selection.states
        found = sum(s.presence == Presence.DETECTED for s in states.values())
        managed = sum(s.status == IntegrationStatus.INSTALLED_MANAGED for s in states.values())
        self.badges[0].configure(text=f"编程端：已检测 {found} 个")
        self.badges[1].configure(text=f"Skill：已安装 {managed} 个")
        health = list(self.service.health_results.values())
        latest = max(health, key=lambda row: row["checked_at"]) if health else None
        self.badges[2].configure(text="MCP：最近验证通过" if latest and latest["ok"] else "MCP：验证失败" if latest else "MCP：未验证", text_color=COLORS["success"] if latest and latest["ok"] else COLORS["secondary"])
        self.health_label.configure(text=f"最近一次验证：{latest['checked_at']} · {latest['tool_count']} 个工具 · {latest.get('code')}" if latest else "MCP 尚未验证 · 点击上方按钮执行真实连接检查")
        self.hint.configure(text="已接入项请使用维护功能。")

    def selection_changed(self):
        # Keep popup open while ticking; only update the compact trigger label.
        count = len(self.selection.selected)
        self.selector.trigger.configure(text=self.selection.states[next(iter(self.selection.selected))].display_name if count == 1 else f"已选择 {count} 个编程端" if count else "选择可安装的编程端")
        self.update_controls()

    def update_controls(self):
        enabled = bool(self.selection.selected and not self.busy)
        self.install_button.configure(state="normal" if enabled else "disabled", fg_color=COLORS["accent"] if enabled else COLORS["card"])
        self.uninstall_button.configure(state="normal" if not self.busy and any(state.receipt_valid for state in self.selection.states.values()) else "disabled")
        for widget in self.action_buttons:
            widget.configure(state="disabled" if self.busy else "normal")
        self.selector.trigger.configure(state="disabled" if self.busy else "normal")

    def install(self):
        ids = sorted(self.selection.selected)
        if self.busy or not ids:
            return
        self.run(lambda: self.service.preview("install", ids), lambda plan: Preview(self, "安装预览", plan, lambda: self.run(lambda: self.service.install_targets(ids, digest=plan["digest"]))), rescan=False)

    def targets(self, operation):
        if not self.busy:
            return Targets(self, operation)
        return None

    def show_paths(self):
        paths(self)

    def open_operator(self, request_id=None):
        if self.approval_store is None:
            return
        from installer.ui.operator_dialog import OperatorCenter
        if self.operator_window and self.operator_window.winfo_exists():
            self.operator_window.request_id = request_id or self.operator_window.request_id
            self.operator_window.refresh()
            self.operator_window.lift()
            self.operator_window.focus_force()
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
                    self.lift()
                    self.focus_force()
            if self.approval_store:
                pending = {row["request_id"] for row in self.approval_store.pending() if not row["approved"]}
                new = pending - self.known_requests
                if new:
                    self.open_operator(next(iter(new)))
                self.known_requests = pending
                self.approval_button.configure(text=f"操作审批 ({len(pending)})" if pending else "操作审批")
        except (OSError, ValueError, KeyError):
            pass
        self.after(1500, self.poll_operator)

    def close(self):
        if self.busy:
            messagebox.showinfo("正在安全处理", "请等待当前维护任务完成，避免中断写入或回滚。", parent=self)
            return
        self.destroy()

    def error_report(self):
        return Diagnostics(self, True)
