from pathlib import Path

import customtkinter as ctk
from PIL import Image

COLORS = {
    "background": ("#EAF4FF", "#121E30"),
    "panel": ("#F6FAFF", "#192A40"),
    "card": ("#E5F0FF", "#223851"),
    "border": ("#CADDF5", "#36516E"),
    "text": ("#102B55", "#E4EEFF"),
    "secondary": ("#516C8B", "#A7BDD6"),
    "accent": ("#075BFF", "#458AFF"),
    "hover": ("#D4E6FF", "#304C6E"),
    "success": ("#087940", "#5FE49C"),
    "warning": ("#945109", "#FFD08A"),
    "error": ("#AE243B", "#FF91A4"),
}
FONT = "Microsoft YaHei UI"


def work_area(widget):
    import os
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def fit_window(widget, width, height, minimum=(600, 430)):
    left, top, right, bottom = work_area(widget)
    scaling = widget._get_window_scaling()
    width = min(width, max(600, int((right - left - 40) / scaling)))
    height = min(height, max(430, int((bottom - top - 65) / scaling)))
    x = left + max(10, int((right - left - width * scaling) / 2))
    y = top + max(10, int((bottom - top - height * scaling - 35) / 2))
    widget.geometry(f"{width}x{height}+{x}+{y}")
    widget.minsize(min(minimum[0], width), min(minimum[1], height))
    return width, height


class Icons:
    def __init__(self, base: Path):
        self.base, self.cache = base / "installer" / "resources" / "icons", {}

    def get(self, name, size=22):
        key = name, size
        if key not in self.cache:
            self.cache[key] = ctk.CTkImage(light_image=Image.open(self.base / f"{name}-light.png"), dark_image=Image.open(self.base / f"{name}-dark.png"), size=(size, size))
        return self.cache[key]


def label(parent, text, *, size=14, bold=False, color="text", **kwargs):
    return ctk.CTkLabel(parent, text=text, text_color=COLORS[color], font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def button(parent, text, command, *, primary=False, icon=None, **kwargs):
    widget = ctk.CTkButton(parent, text=text, command=command, height=42, corner_radius=8, font=(FONT, 14), fg_color=COLORS["accent"] if primary else COLORS["panel"], hover_color=("#0048D8", "#256CDD") if primary else COLORS["hover"], text_color="#FFFFFF" if primary else COLORS["text"], text_color_disabled=COLORS["secondary"], border_width=0 if primary else 1, border_color=COLORS["border"], image=icon, compound="left", **kwargs)
    widget._canvas.configure(takefocus=True)
    def invoke(_event):
        if widget.cget("state") == "normal":
            command()
        return "break"
    widget._canvas.bind("<Return>", invoke)
    widget._canvas.bind("<space>", invoke)
    widget._canvas.bind("<FocusIn>", lambda _: widget.configure(border_width=2))
    widget._canvas.bind("<FocusOut>", lambda _: widget.configure(border_width=0 if primary else 1))
    return widget


def accessible_checkbox(widget):
    widget._canvas.configure(takefocus=True)
    def toggle(_event):
        widget.toggle()
        return "break"
    widget._canvas.bind("<space>", toggle)
    widget._canvas.bind("<Return>", toggle)
    return widget


def panel(parent, **kwargs):
    return ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_color=COLORS["border"], border_width=1, corner_radius=12, **kwargs)
