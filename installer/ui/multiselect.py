"""Persistent multi-selection in an elevated, distinct glass popup."""
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QCheckBox, QDialog, QScrollArea, QWidget
from installer.ui.theme import GlassFrame, button, column, label, row


class MultiSelect(QWidget):
    def __init__(self, parent, model, on_change):
        super().__init__(parent)
        self.model, self.on_change = model, on_change
        self.popup, self.checks = None, {}
        self.trigger = button("选择可安装的编程端", self.toggle, icon="chevron")
        self.trigger.setAccessibleName("选择可安装的编程端，可多选")
        column(self, 0, 0).addWidget(self.trigger)

    def refresh(self):
        count = len(self.model.selected)
        self.trigger.setText(self.model.states[next(iter(self.model.selected))].display_name if count == 1 else f"已选择 {count} 个编程端" if count else "选择可安装的编程端")
        for key, checkbox in self.checks.items():
            state = self.model.states.get(key)
            checkbox.blockSignals(True)
            checkbox.setChecked(key in self.model.selected)
            checkbox.setEnabled(bool(state and state.install_eligible))
            checkbox.blockSignals(False)

    def toggle(self):
        if self.popup and self.popup.isVisible():
            self.popup.close()
            return
        popup = QDialog(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.setWindowTitle("选择安装目标")
        frame = GlassFrame(popup, popup=True)
        column(popup, 5, 0).addWidget(frame)
        body = column(frame, 18, 10)
        body.addWidget(label("添加编程端", size=16, bold=True))
        body.addWidget(label("仅对已检测到、尚未接入的客户端安装", role="secondary", wrap=True))
        scroll, content = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        items = column(content, 0, 7)
        self.checks = {}
        for key, state in self.model.states.items():
            entry = GlassFrame()
            stack = column(entry, 8, 0)
            checkbox = QCheckBox(state.display_name)
            checkbox.setChecked(key in self.model.selected)
            checkbox.setEnabled(state.install_eligible)
            checkbox.toggled.connect(lambda checked, k=key: self.choose(k, checked))
            stack.addWidget(checkbox)
            stack.addWidget(label(state.label, size=11, role="secondary"))
            items.addWidget(entry)
            self.checks[key] = checkbox
        items.addStretch()
        scroll.setWidget(content)
        body.addWidget(scroll, 1)
        body.addWidget(row(button("全选可安装项", self.select_all, compact=True), None, button("完成", popup.close, primary=True, compact=True)))
        available = self.screen().availableGeometry()
        popup.resize(min(max(self.width() + 36, 350), available.width() - 20), min(545, available.height() - 30))
        position = self.mapToGlobal(QPoint(0, self.height() + 7))
        x = max(available.left() + 5, min(position.x(), available.right() - popup.width() - 5))
        y = position.y() if position.y() + popup.height() < available.bottom() else max(available.top() + 5, self.mapToGlobal(QPoint()).y() - popup.height() - 7)
        popup.move(x, y)
        self.popup = popup
        popup.finished.connect(self._closed)
        popup.show()

    def _closed(self):
        self.checks = {}
        if self.popup:
            self.popup.deleteLater()
        self.popup = None

    def choose(self, key, checked):
        self.model.choose(key, checked)
        self.refresh()
        self.on_change()

    def select_all(self):
        self.model.select_all()
        self.refresh()
        self.on_change()
