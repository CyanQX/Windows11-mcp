"""Local human approval center. Opening or refreshing can never grant authority."""

import json
import time
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

from installer.ui.dialogs import Detail, text_box
from installer.ui.theme import button, label


class OperatorCenter(Detail):
    def __init__(self, owner, store, request_id=None):
        super().__init__(owner, "本机操作审批", "850x680")
        self.store, self.request_id = store, request_id
        label(self, "本机操作审批", size=22, bold=True).pack(anchor="w", padx=24, pady=(20, 4))
        label(self, "由你核对具体操作后批准一次；仅限原会话、原参数、有效期内使用。", size=13, color="secondary").pack(anchor="w", padx=24)
        self.selector = ctk.CTkOptionMenu(self, values=["暂无待审请求"], command=self.select, width=760)
        self.selector.pack(fill="x", padx=22, pady=(16, 4))
        self.details = text_box(self, "正在读取本机待审请求…")
        self.expiry = label(self, "", size=12, color="warning")
        self.expiry.pack(anchor="w", padx=24)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=22, pady=18)
        self.reject_button = button(actions, "拒绝本次操作", lambda: self.decide(False), width=160)
        self.reject_button.pack(side="left")
        self.approve_button = button(actions, "批准一次", lambda: self.decide(True), primary=True, width=180)
        self.approve_button.pack(side="right")
        self.refresh()
        from windows_mcp.infrastructure.operator_ui import mark_operator_window
        mark_operator_window(self)
        self.tick()

    def refresh(self):
        self.rows = {row["request_id"]: row for row in self.store.pending()}
        self.mapping = {f"{row['tool']} · {'已批准待使用' if row['approved'] else '等待确认'} · {key[:10]}": key for key, row in self.rows.items()}
        self.selector.configure(values=list(self.mapping) or ["暂无待审请求"])
        if self.request_id not in self.rows:
            self.request_id = next(iter(self.rows), None)
        title = next((title for title, key in self.mapping.items() if key == self.request_id), "暂无待审请求")
        self.selector.set(title)
        self.show()

    def select(self, title):
        self.request_id = self.mapping.get(title)
        self.show()

    def show(self):
        row = self.rows.get(self.request_id)
        active = bool(row and not row["approved"] and row["expires_at"] > time.time())
        for widget in (self.approve_button, self.reject_button):
            widget.configure(state="normal" if active else "disabled")
        value = "暂无待审请求。AI 请求受保护操作时，此窗口会自动打开。"
        if row:
            try:
                arguments = self.store.operator_arguments(self.request_id)
                value = {"请求": self.request_id, "工具": row["tool"], "风险等级": row["risk"], "原因（来自请求方，请自行核对）": row["description"], "会话": row["session_id"], "精确操作 SHA-256": row["action_hash"], "截止时间": datetime.fromtimestamp(row["expires_at"]).astimezone().isoformat(timespec="seconds"), "具体参数（本机查看）": arguments if arguments is not None else row["scope"], "说明": "批准仅适用于以上原始参数；不授权后续其他操作。" if arguments is not None else "旧版请求缺少完整参数。请拒绝并让 AI 重新发起请求。"}
                if arguments is None:
                    self.approve_button.configure(state="disabled")
            except Exception as exc:
                value = f"请求校验失败：{type(exc).__name__}；未授予权限。"
                self.approve_button.configure(state="disabled")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        self.details.configure(state="disabled")

    def decide(self, approve):
        try:
            # Re-read signed content and expiry immediately before a human decision.
            row = self.store.read(self.request_id)
            if row["approved"]:
                return
            if approve and self.store.operator_arguments(self.request_id) is None:
                return
            if not messagebox.askyesno("确认本次决定", f"{'批准一次' if approve else '拒绝'} {row['tool']} 请求 {self.request_id[:10]}？\n请确认你已核对上方具体参数。", parent=self):
                return
            if approve:
                self.store.approve(self.request_id)
            else:
                self.store.reject(self.request_id)
            self.destroy()
        except Exception as exc:
            messagebox.showerror("请求已变化", f"未授予新权限：{exc}", parent=self)
            self.refresh()

    def tick(self):
        if not self.winfo_exists():
            return
        row = self.rows.get(self.request_id)
        remaining = max(0, int(row["expires_at"] - time.time())) if row else 0
        self.expiry.configure(text=f"剩余 {remaining} 秒 · {'已批准，等待原会话使用' if row and row['approved'] else '等待你的决定'}" if row else "")
        self.refresh()
        self.after(1500, self.tick)
