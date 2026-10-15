"""One installation selector; disabled state is enforced by the selection model."""

import customtkinter as ctk

from installer.models import Presence, IntegrationStatus
from installer.ui.theme import COLORS, FONT, button, label, accessible_checkbox, work_area


class MultiSelect(ctk.CTkFrame):
    def __init__(self, parent, model, icons, changed):
        super().__init__(parent, fg_color="transparent")
        self.model, self.icons, self.changed = model, icons, changed
        self.popup = None
        self.trigger = button(self, "选择编程端", self.toggle, icon=icons.get("chevron", 18), anchor="w", width=200)
        self.trigger.pack(fill="x")

    def refresh(self):
        names = [self.model.states[key].display_name for key in self.model.selected]
        self.trigger.configure(text=names[0] if len(names) == 1 else f"已选择 {len(names)} 个编程端" if names else "选择可安装的编程端")
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
            self.popup = None

    def toggle(self):
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
            self.popup = None
            return
        popup = self.popup = ctk.CTkToplevel(self)
        popup.title("选择安装目标")
        popup.overrideredirect(True)
        popup.configure(fg_color=COLORS["panel"])
        popup.transient(self.winfo_toplevel())
        popup.resizable(False, False)
        width = max(360, self.winfo_width())
        left, top, right, bottom = work_area(popup)
        scaling = popup._get_window_scaling()
        height = min(430, int((bottom - top - 65) / scaling))
        x = min(self.winfo_rootx(), int(right - width * scaling - 10))
        y = self.winfo_rooty() + self.winfo_height() + 8
        if y + height * scaling + 40 > bottom:
            y = max(top + 10, int(self.winfo_rooty() - height * scaling - 40))
        popup.geometry(f"{width}x{height}+{x}+{y}")
        label(popup, "可多选 · 已接入目标请使用维护功能", size=12, color="secondary").pack(anchor="w", padx=18, pady=(15, 8))
        body = ctk.CTkScrollableFrame(popup, fg_color="transparent", height=265)
        body.pack(fill="both", expand=True, padx=8)
        self.checks = {}
        for key, state in self.model.states.items():
            row = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=8)
            row.pack(fill="x", pady=4, padx=4)
            variable = ctk.BooleanVar(value=key in self.model.selected)
            available = state.install_eligible
            status = state.label if state.status != IntegrationStatus.AVAILABLE else "可安装" if available else "路径无效" if state.presence == Presence.PATH_INVALID else "未检测到客户端"
            checkbox = ctk.CTkCheckBox(row, text=f"{state.display_name}\n{status}", variable=variable, state="normal" if available else "disabled", command=lambda k=key, v=variable: self.choose(k, v.get()), font=(FONT, 13), checkbox_width=19, checkbox_height=19, text_color=COLORS["text"], text_color_disabled=COLORS["secondary"], fg_color=COLORS["accent"], border_color=COLORS["border"])
            checkbox.pack(fill="x", padx=14, pady=12)
            accessible_checkbox(checkbox)
            self.checks[key] = checkbox, variable
        actions = ctk.CTkFrame(popup, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=12)
        button(actions, "全选可安装项", self.select_all, width=145).pack(side="left")
        button(actions, "完成", self.finish, primary=True, width=110).pack(side="right")
        popup.bind("<Escape>", lambda _: self.finish())
        popup.bind("<Control-a>", lambda _: self.select_all())
        popup.after(50, lambda: (popup.lift(), popup.focus_force()))

    def choose(self, key, selected):
        if not self.model.choose(key, selected):
            self.checks[key][1].set(False)
        self.changed()

    def select_all(self):
        self.model.select_all()
        for key, (_, variable) in self.checks.items():
            variable.set(key in self.model.selected)
        self.changed()

    def finish(self):
        self.refresh()
        self.changed()
