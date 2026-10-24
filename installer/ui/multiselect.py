"""Persistent multi-selection in an elevated, distinct glass popup."""
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea, QWidget
from installer.ui.controls import GlassCheckBox as QCheckBox
from installer.ui.theme import GlassFrame, button, column, label, row


class MultiSelect(QWidget):
    def __init__(self, parent, model, on_change):
        super().__init__(parent)
        self.model, self.on_change = model, on_change
        self.popup, self.checks, self.entries, self.statuses = None, {}, {}, {}
        self._trigger_dismiss = False
        QApplication.instance().installEventFilter(self)
        self.trigger = button("选择可安装的编程端", self.toggle, icon="chevron")
        self.trigger.setAccessibleName("选择可安装的编程端，可多选")
        column(self, 0, 0).addWidget(self.trigger)

    def refresh(self):
        count = len(self.model.selected)
        if self.popup:
            self.clear_button.setEnabled(bool(count))
        self.trigger.setText(self.model.states[next(iter(self.model.selected))].display_name if count == 1 else f"已选择 {count} 个编程端" if count else "选择可安装的编程端")
        for key, checkbox in self.checks.items():
            state = self.model.states.get(key)
            checkbox.blockSignals(True)
            checkbox.setChecked(key in self.model.selected)
            checkbox.setEnabled(bool(state and state.install_eligible))
            checkbox.setProperty("installed", bool(state and state.receipt_valid))
            checkbox.setAccessibleDescription("已安装" if state and state.receipt_valid else state.label if state else "")
            checkbox.update()
            checkbox.blockSignals(False)
            self.entries[key].setProperty("selected", checkbox.isChecked())
            self.entries[key].update()
            if state:
                self.statuses[key].setText(state.label)
                status = self.statuses[key]
                role = "success" if state.receipt_valid else "secondary"
                if status.property("role") != role:
                    status.setProperty("role", role)
                    status.style().unpolish(status)
                    status.style().polish(status)

    def toggle(self):
        if self._trigger_dismiss:
            self._trigger_dismiss = False
            return
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
        scroll, content = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        items = column(content, 0, 7)
        self.checks = {}
        self.entries, self.statuses = {}, {}
        for key, state in self.model.states.items():
            if key == "opencode":
                continue
            entry = GlassFrame()
            stack = column(entry, 6, 0)
            checkbox = QCheckBox(state.display_name)
            checkbox.setChecked(key in self.model.selected)
            checkbox.setEnabled(state.install_eligible)
            checkbox.setProperty("installed", state.receipt_valid)
            checkbox.setAccessibleDescription("已安装" if state.receipt_valid else state.label)
            checkbox.toggled.connect(lambda checked, k=key: self.choose(k, checked))
            status = label(state.label, size=10, role="success" if state.receipt_valid else "secondary")
            status.setProperty("badge", True)
            status.setFixedHeight(28)
            status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stack.addWidget(row(checkbox, status))
            entry.setProperty("selected", checkbox.isChecked())
            items.addWidget(entry)
            self.checks[key] = checkbox
            self.entries[key], self.statuses[key] = entry, status
        items.addStretch()
        scroll.setWidget(content)
        body.addWidget(scroll, 1)
        self.clear_button = button("清除所选", self.clear_selection, compact=True)
        self.clear_button.setEnabled(bool(self.model.selected))
        body.addWidget(row(button("全选可安装项", self.select_all, compact=True), self.clear_button, None, button("完成", popup.close, primary=True, compact=True)))
        available = self.screen().availableGeometry()
        popup.resize(min(max(self.width(), 350), available.width() - 20), min(420, 140 + len(self.model.states) * 60, available.height() - 30))
        position = self.trigger.mapToGlobal(QPoint((self.trigger.width() - popup.width()) // 2, self.trigger.height() + 7))
        x = max(available.left() + 5, min(position.x(), available.right() - popup.width() - 5))
        y = position.y() if position.y() + popup.height() < available.bottom() else max(available.top() + 5, self.mapToGlobal(QPoint()).y() - popup.height() - 7)
        popup.move(x, y)
        self.popup = popup
        popup.finished.connect(self._closed)
        popup.show()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            position = self.trigger.mapFromGlobal(event.globalPosition().toPoint())
            if self.popup and self.popup.isVisible() and self.trigger.rect().contains(position):
                # Qt dismisses a Popup before replaying the press to its trigger.
                self._trigger_dismiss = True
                self.popup.close()
            elif not self.trigger.rect().contains(position):
                self._trigger_dismiss = False
        return super().eventFilter(watched, event)

    def _closed(self):
        self.checks = {}
        self.entries, self.statuses = {}, {}
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

    def clear_selection(self):
        self.model.clear_selection()
        self.refresh()
        self.on_change()
