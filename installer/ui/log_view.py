import customtkinter as ctk

from installer.ui.theme import COLORS


class LogView(ctk.CTkTextbox):
    def __init__(self, parent):
        super().__init__(parent, fg_color="#101C2C", text_color="#DAE8FF", font=("Consolas", 14), corner_radius=9, wrap="word", scrollbar_button_color="#405975", scrollbar_button_hover_color="#5C7C9E", border_width=1, border_color=COLORS["border"])
        for name, color in {"INFO": "#A7CBFF", "SUCCESS": "#66E69A", "WARNING": "#FFD08A", "ERROR": "#FF91A4"}.items():
            self.tag_config(name, foreground=color)
        self.configure(state="disabled")
        self.lines = 0

    def append(self, row):
        self.configure(state="normal")
        stamp = row["time_iso"][11:19]
        target = f" [{row['target_id']}]" if row.get("target_id") else ""
        code = f" · {row['error_code']}" if row.get("error_code") else ""
        self.insert("end", f"[{stamp}] {row['level']}{target}  {row['message']}{code}\n", row["level"])
        self.lines += 1
        if self.lines > 1500:
            self.delete("1.0", "101.0")
            self.lines -= 100
        self.configure(state="disabled")
        self.see("end")

    def clear(self):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")
        self.lines = 0

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.get("1.0", "end-1c"))
