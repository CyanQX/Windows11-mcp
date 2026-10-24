"""Glass materials and accessible Qt controls; appearance never rebuilds services."""
from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget

FONT = "Microsoft YaHei UI"
PALETTES = {
    "light": dict(bg="#F7F8FA", end="#FFFFFF", text="#203747", secondary="#4C6577", accent="#41667F", border="#CCD2D8", success="#087645", warning="#8E5A13", error="#B52B48", glass="#FCFCFD"),
    "warm": dict(bg="#ECE7D9", end="#FAF5E9", text="#3C4238", secondary="#656D59", accent="#446D58", border="#CECEB9", success="#256341", warning="#805523", error="#AD3744", glass="#FFFBEF"),
    "dark": dict(bg="#131F33", end="#243852", text="#ECF3FF", secondary="#B1C3D9", accent="#86B5FF", border="#48617E", success="#76DEB1", warning="#F5CE8B", error="#FF9DB2", glass="#233751"),
}


class Theme:
    def __init__(self, settings, base):
        self.settings, self.base = settings, base
        self.mode, self.colors = "light", PALETTES["light"]

    def apply(self):
        mode = self.settings.value["theme"]
        if mode == "system":
            mode = "dark" if QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
        self.mode, self.colors = mode, PALETTES[mode]
        c, app = self.colors, QApplication.instance()
        app.setProperty("managerTheme", self)
        palette = app.palette()
        for role, color in ((QPalette.ColorRole.Window, c["bg"]), (QPalette.ColorRole.Base, c["glass"]), (QPalette.ColorRole.Text, c["text"]), (QPalette.ColorRole.WindowText, c["text"]), (QPalette.ColorRole.ButtonText, c["text"]), (QPalette.ColorRole.Highlight, c["accent"])):
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setFont(QFont(FONT, 10))
        fill = "rgba(245,251,255,190)" if mode != "dark" else "rgba(45,65,92,220)"
        hover = "#D8E9FF" if mode == "light" else "#E6E8D7" if mode == "warm" else "#3A5475"
        tactile_rules = """
TactileGlassButton, TactileGlassButton:hover, TactileGlassButton:pressed, TactileGlassButton:focus, TactileGlassButton:disabled,
TactileGlassButton[primary='true'], TactileGlassButton[primary='true']:hover, TactileGlassButton[compact='true'], TactileGlassButton[compact='true'][primary='true'] {
    background: transparent; border: none; padding: 0px;
}
""" if self.appearance["glass_strength"] else ""
        app.setStyleSheet(f"""
QWidget {{ color: {c['text']}; font-family: '{FONT}'; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QLabel {{ background: transparent; border: none; }}
QLabel[stepValue='true'] {{ background: {fill}; border: 1px solid {c['border']}; border-radius: 9px; padding: 9px 12px; }}
QLabel[role='secondary'] {{ color: {c['secondary']}; }}
QLabel[role='success'] {{ color: {c['success']}; }}
QLabel[role='warning'] {{ color: {c['warning']}; }}
QLabel[role='error'] {{ color: {c['error']}; }}
QLabel[badge='true'] {{ background: {hover}; padding: 4px 8px; border-radius: 8px; }}
QPushButton {{ background: {fill}; border: 1px solid {c['border']}; border-radius: 12px; padding: 10px 16px; min-height: 20px; font-weight: 500; }}
QPushButton:hover {{ background: {hover}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus, QComboBox:focus, QLineEdit:focus {{ border: 2px solid {c['accent']}; }}
QPushButton[primary='true'] {{ background: #1266ED; color: white; border-color: #1266ED; font-weight: 600; }}
QPushButton[primary='true']:hover {{ background: #2379FC; }}
QPushButton:disabled {{ color: {c['secondary']}; background: transparent; border-color: {c['border']}; }}
QPushButton[compact='true'] {{ padding: 5px 9px; min-height: 16px; border-radius: 9px; background: transparent; }}
QPushButton[compact='true'][primary='true'] {{ background: #1266ED; color: #FFFFFF; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QLineEdit, QComboBox, QSpinBox {{ background: {fill}; border: 1px solid {c['border']}; border-radius: 9px; padding: 9px; min-height: 22px; selection-background-color: {c['accent']}; }}
QSpinBox {{ padding: 9px 12px; }}
QSpinBox:focus {{ border: 2px solid {c['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; }}
QComboBox QAbstractItemView {{ background: {c['glass']}; selection-background-color: {c['border']}; border: 1px solid {c['border']}; padding: 8px; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 4px 8px; border-radius: 7px; }}
QComboBox::drop-down {{ border: none; width: 30px; subcontrol-origin: padding; subcontrol-position: top right; }}
QComboBox::down-arrow {{ image: url('{(self.base / 'installer/resources/icons/chevron-light.png').as_posix()}'); width: 14px; height: 14px; }}
QPlainTextEdit, QTextBrowser {{ background: {c['glass']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 12px; selection-background-color: {c['border']}; }}
QProgressBar {{ background: {c['border']}; border: none; border-radius: 4px; min-height: 7px; max-height: 7px; }}
QProgressBar::chunk {{ background: #1266ED; border-radius: 4px; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ padding: 10px 18px; background: transparent; color: {c['secondary']}; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
QSlider::groove:horizontal {{ height: 6px; background: {c['border']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 18px; margin: -6px 0; background: {c['accent']}; border-radius: 9px; }}
QToolTip {{ color: {c['text']}; background: {c['glass']}; border: 1px solid {c['border']}; padding: 8px; }}
{tactile_rules}
TactileGlassButton[transparent='true'], TactileGlassButton[transparent='true']:hover, TactileGlassButton[transparent='true']:pressed, TactileGlassButton[transparent='true']:focus {{ background: transparent; border: none; padding: 5px 9px; }}
QPushButton[detailToggle='true'] {{ min-height: 44px; padding-top: 0px; padding-bottom: 0px; }}
""")
        for widget in app.topLevelWidgets():
            widget.update()
            backdrop(widget, mode == "dark")

    def icon(self, name):
        variant = "dark" if self.mode == "dark" else "light"
        return QIcon(str(self.base / "installer/resources/icons" / f"{name}-{variant}.png"))

    @property
    def appearance(self):
        return self.settings.value["appearance"]


def theme():
    return QApplication.instance().property("managerTheme")


def draw_glass(widget, *, popup=False):
    from installer.ui.tactile_material import paint_panel
    current = theme()
    if not current:
        return
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(widget.rect()).adjusted(1, 1, -1, -1)
    radius = widget.property("cornerRadius")
    radius = 20 if radius is None else float(radius)
    if current.appearance["glass_strength"]:
        paint_panel(painter, rect, radius, popup=popup, selected=bool(widget.property("selected")))
    else:
        painter.setBrush(QColor(current.colors["glass"]))
        painter.setPen(QColor(current.colors["border"]))
        painter.drawRoundedRect(rect, radius, radius)


class GlassFrame(QFrame):
    def __init__(self, parent=None, *, popup=False):
        super().__init__(parent)
        self.popup = popup

    def paintEvent(self, event):
        draw_glass(self, popup=self.popup)


def label(text, *, size=13, bold=False, role=None, wrap=False):
    item = QLabel(text)
    item.setFont(QFont(FONT, size, QFont.Weight.DemiBold if bold else QFont.Weight.Normal))
    item.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        item.setProperty("role", role)
    item.setWordWrap(wrap)
    item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return item


def button(text, callback=None, *, icon=None, primary=False, compact=False):
    from installer.ui.tactile_button import TactileGlassButton
    item = TactileGlassButton(text, tone="primary" if primary else "neutral")
    item.setProperty("primary", primary)
    item.setProperty("compact", compact)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    item.setMinimumHeight(44)
    if icon:
        item.setProperty("iconName", icon)
        item.setIcon(theme().icon(icon))
        item.setIconSize(QSize(20, 20))
    if callback:
        item.clicked.connect(lambda checked=False: callback())
    return item


def column(widget, margin=20, spacing=12):
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return layout


def row(*items):
    widget, layout = QWidget(), QHBoxLayout()
    widget.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for item in items:
        layout.addStretch() if item is None else layout.addWidget(item)
    return widget


def backdrop(window, dark=False):
    if os.name != "nt" or not window.isVisible():
        return
    try:
        from ctypes import wintypes
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        for attr, value in ((20, int(dark)), (33, 2), (38, 2)):
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(int(window.winId()), attr, ctypes.byref(data), ctypes.sizeof(data))
    except OSError:
        pass


def application():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setApplicationName("Windows-MCP")
    app.setOrganizationName("CyanQX")
    return app
