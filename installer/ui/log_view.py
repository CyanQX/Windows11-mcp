"""Bounded batch rendering, preserving user scroll intent and semantic colors."""
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from installer.ui.theme import theme


class LogView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1500)
        self.setFont(QFont("Cascadia Mono", 10))
        self.setPlaceholderText("准备就绪。选择编程端后安装，或检查已有接入。")
        self.apply_appearance()

    def apply_appearance(self):
        value = theme().appearance
        color = {"blue": "#E8F3FF", "warm": "#F5F0E3", "dark": "#18283D", "custom": value["log_custom_color"]}[value["log_background"]]
        channels = QColor(color).getRgbF()[:3]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        luminance = sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))
        dark = luminance < 0.179
        self.levels = ["INFO", "SUCCESS", "WARNING", "ERROR"]
        self.tones = ["#325A86", "#087040", "#825008", "#B12B45"] if not dark else ["#C1D9F8", "#7CE3B4", "#F4D09A", "#FFA6B8"]
        if value["log_background"] == "custom":
            self.tones = ["#FFFFFF" if dark else "#000000"] * 4
        self.setStyleSheet(f"QPlainTextEdit {{ background: {color}; color: {self.tones[0]}; border: 1px solid {theme().colors['border']}; border-radius: 14px; padding: 14px; }}")
        block = self.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self.tones[max(0, min(3, block.userState()))]))
            cursor.setCharFormat(fmt)
            block = block.next()

    def append(self, event):
        self.append_rows([event])

    def append_rows(self, rows):
        scroll = self.verticalScrollBar()
        at_end = scroll.maximum() - scroll.value() < 12
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        for event in rows:
            level = self.levels.index(event.get("level")) if event.get("level") in self.levels else 0
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self.tones[level]))
            if not self.document().isEmpty():
                cursor.insertBlock()
            text = f"{event['time_iso'][11:19]}  {self.levels[level]:<7}  {event['message']}"
            if event.get("target_id"):
                text += f"  · {event['target_id']}"
            if event.get("error_code"):
                text += f"  [{event['error_code']}]"
            cursor.insertText(text, fmt)
            cursor.block().setUserState(level)
        cursor.endEditBlock()
        if at_end:
            scroll.setValue(scroll.maximum())

    def copy_all(self):
        QApplication.clipboard().setText(self.toPlainText())
