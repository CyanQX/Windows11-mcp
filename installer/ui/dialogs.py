"""Independent maintenance selections, immutable previews and explicit confirmation."""

import json
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from installer.models import IntegrationStatus
from installer.ui.theme import COLORS, FONT, button, label, accessible_checkbox, fit_window


class Detail(ctk.CTkToplevel):
    def __init__(self, owner, title, size="760x590"):
        super().__init__(owner)
        self.owner = owner
        self.title(title)
        width, height = (int(part) for part in size.split("x"))
        fit_window(self, width, height)
        self.configure(fg_color=COLORS["background"])
        self.transient(owner)
        self.after(80, lambda: (self.lift(), self.focus_force()))
        self.bind("<Escape>", lambda _: self.destroy())


def text_box(parent, value, *, height=320):
    box = ctk.CTkTextbox(parent, fg_color=COLORS["panel"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text"], font=(FONT, 12), wrap="word", height=height)
    box.pack(fill="both", expand=True, padx=20, pady=12)
    box.insert("1.0", value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    box.configure(state="disabled")
    return box


class Preview(Detail):
    def __init__(self, owner, title, plan, apply=None, *, warning="确认后才会执行；若文件在预览后发生变化，将停止操作。"):
        super().__init__(owner, title)
        label(self, title, size=20, bold=True).pack(anchor="w", padx=22, pady=(18, 4))
        label(self, warning, size=12, color="secondary", wraplength=690, justify="left").pack(anchor="w", padx=22)
        self.body = text_box(self, plan)
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=(0, 18))
        button(controls, "关闭" if apply is None else "取消", self.destroy, width=110).pack(side="right", padx=6)
        if apply:
            button(controls, "确认执行", lambda: (self.destroy(), apply()), primary=True, width=145).pack(side="right")


class Targets(Detail):
    def __init__(self, owner, operation):
        names = {"uninstall": "卸载 Skill + MCP", "repair": "智能修复", "verify": "验证 MCP 连接", "backup": "备份配置", "adopt": "接管外部接入", "upgrade": "统一升级受管接入"}
        super().__init__(owner, names[operation], "700x580")
        self.operation, self.variables = operation, {}
        label(self, names[operation], size=21, bold=True).pack(anchor="w", padx=24, pady=(20, 6))
        hint = "请选择目标，下一步查看逐文件预览。" if operation != "verify" else "执行真实 stdio 握手，核对版本、31 个工具及 Session 状态。"
        label(self, hint, size=12, color="secondary").pack(anchor="w", padx=24)
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=12)
        for key, state in owner.selection.states.items():
            eligible = state.status == IntegrationStatus.EXTERNAL if operation == "adopt" else state.receipt_valid if operation in {"uninstall", "repair", "verify", "upgrade"} else state.status != IntegrationStatus.UNKNOWN
            if not eligible:
                continue
            variable = ctk.BooleanVar(value=False)
            self.variables[key] = variable
            item = ctk.CTkFrame(body, fg_color=COLORS["panel"], corner_radius=9, border_width=1, border_color=COLORS["border"])
            item.pack(fill="x", pady=5)
            checkbox = ctk.CTkCheckBox(item, text=f"{state.display_name}   ·   {state.label}", variable=variable, font=(FONT, 14), text_color=COLORS["text"], fg_color=COLORS["accent"], border_color=COLORS["border"])
            checkbox.pack(anchor="w", padx=16, pady=16)
            accessible_checkbox(checkbox)
        if not self.variables:
            label(body, "没有符合条件的目标。请先检查安装状态。", color="secondary").pack(pady=40)
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=24, pady=18)
        button(controls, "取消", self.destroy, width=110).pack(side="right", padx=(10, 0))
        self.next_button = button(controls, "开始验证" if operation == "verify" else "创建备份" if operation == "backup" else "查看操作预览", self.preview, primary=True, width=170)
        self.next_button.pack(side="right")

    def preview(self):
        ids = [key for key, var in self.variables.items() if var.get()]
        if not ids:
            messagebox.showinfo("选择目标", "请至少选择一个目标。", parent=self)
            return
        owner, operation = self.owner, self.operation
        try:
            if operation in {"verify", "backup"}:
                self.destroy()
                owner.run(lambda: [owner.service.verify(key).to_dict() for key in ids] if operation == "verify" else owner.service.backup(ids))
            else:
                def prepare():
                    if operation == "upgrade" and set(ids) != set(owner.service.upgrade_targets()):
                        raise ValueError("请勾选全部受管接入，统一升级共享服务。")
                    return owner.service.preview(operation, ids)
                self.destroy()
                owner.run(prepare, lambda plan: Preview(owner, "操作预览 · " + operation, plan, lambda: owner.run(lambda: owner.service.apply_plan(operation, ids, plan["digest"]))), rescan=False)
        except Exception as exc:
            messagebox.showerror("无法开始", str(exc), parent=owner)


class Diagnostics(Detail):
    def __init__(self, owner, report=False):
        super().__init__(owner, "错误报告预览" if report else "历史日志与诊断", "860x640")
        self.report_mode = report
        label(self, "错误报告预览" if report else "历史日志与诊断", size=21, bold=True).pack(anchor="w", padx=22, pady=(18, 6))
        label(self, "导出内容：版本、脱敏检测结论、连接结果、最近事件；不包含完整配置、凭证或私人文件。", size=12, color="secondary", wraplength=800, justify="left").pack(anchor="w", padx=22)
        self.query = ctk.CTkEntry(self, placeholder_text="搜索会话 / 客户端 / SUCCESS / ERROR / 错误码", font=(FONT, 12))
        if not report:
            self.query.pack(fill="x", padx=22, pady=10)
            self.query.bind("<KeyRelease>", lambda _: self.refresh())
        self.body = text_box(self, "")
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=16)
        button(controls, "复制", self.copy, width=110).pack(side="left")
        button(controls, "导出已预览内容", self.export, primary=True, width=180).pack(side="right")
        self.refresh()

    def refresh(self):
        self.value = self.owner.service.log.report(self.owner.selection.states, self.owner.service.health_results) if self.report_mode else self.owner.service.log.history(self.query.get())
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", json.dumps(self.value, ensure_ascii=False, indent=2))
        self.body.configure(state="disabled")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.body.get("1.0", "end-1c"))

    def export(self):
        target = filedialog.asksaveasfilename(parent=self, title="导出脱敏报告", defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile=f"Windows-MCP-report-{datetime.now():%Y%m%d-%H%M%S}.json")
        if target:
            from installer.backend import atomic_write
            from installer.storage import check_path
            from pathlib import Path
            try:
                atomic_write(check_path(Path(target)), json.dumps(self.value, ensure_ascii=False, indent=2).encode("utf-8"))
                messagebox.showinfo("导出完成", "已保存预览中的脱敏内容。", parent=self)
            except Exception as exc:
                messagebox.showerror("导出失败", str(exc), parent=self)
