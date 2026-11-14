"""Fixed manager panels; window resizing never changes their placement."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget, QBoxLayout

from installer.ui.theme import GlassFrame, label


class FixedPanel(GlassFrame):
    def __init__(self, parent, key, title, content):
        super().__init__(parent)
        self.setObjectName("panel-" + key)
        self.setWindowTitle(title)
        self.content = content
        if key in {"targets", "maintenance"}:
            self.setProperty("cornerRadius", 0)
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.heading = label(title, size=17, bold=True)
        self.heading.setContentsMargins(20, 14, 16, 5)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        body.addWidget(self.heading)
        body.addWidget(content, 1)


class Workspace(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setObjectName("fixed-workspace")
        self.panels = {}
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(12, 0, 12, 12)
        self.grid.setSpacing(12)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(0, 1)

    def add(self, key, title, content):
        panel = FixedPanel(self, key, title, content)
        self.panels[key] = panel
        if key == "targets":
            panel.setFixedWidth(300)
            self.grid.addWidget(panel, 0, 0, 2, 1)
        elif key == "maintenance":
            self.grid.addWidget(panel, 0, 1)
        elif key == "actions":
            panel.setFixedHeight(128)
            self.grid.addWidget(panel, 1, 1)
        else:
            raise ValueError("Unknown workspace panel")
        return panel

    def reset(self):
        for panel in self.panels.values():
            panel.show()
        self.grid.activate()

    def load(self, _saved):
        # Old dock positions, floating windows and hidden edges are retired.
        self.reset()

    def snapshot(self):
        return {"schema": 2, "mode": "fixed"}

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if len(self.panels) != 3:
            return
        compact = self.width() < 900
        if getattr(self, "compact", None) == compact:
            return
        self.compact = compact
        for panel in self.panels.values():
            self.grid.removeWidget(panel)
        targets = self.panels["targets"]
        targets.setMinimumWidth(0 if compact else 300)
        targets.setMaximumWidth(16777215 if compact else 300)
        target_layout = targets.content.layout()
        target_layout.setDirection(QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom)
        target_layout.setContentsMargins(16, 8, 16, 12) if compact else target_layout.setContentsMargins(20, 20, 20, 20)
        targets.heading.setVisible(not compact)
        targets.setMaximumHeight(76 if compact else 16777215)
        actions = self.panels["actions"]
        actions.heading.setVisible(not compact)
        actions.setFixedHeight(84 if compact else 128)
        actions.content.layout().setContentsMargins(16, 16, 16, 16) if compact else actions.content.layout().setContentsMargins(16, 20, 16, 20)
        maintenance = self.panels["maintenance"].content.layout()
        maintenance.setContentsMargins(16, 12, 16, 12) if compact else maintenance.setContentsMargins(20, 20, 20, 20)
        maintenance.setSpacing(8 if compact else 12)
        self.owner.task_strip.setFixedHeight(44 if compact else 58)
        self.panels["maintenance"].content.setMinimumHeight(maintenance.minimumSize().height())
        if compact:
            self.owner.centralWidget().layout().addWidget(actions)
            self.grid.addWidget(targets, 0, 0, 1, 2)
            self.grid.addWidget(self.panels["maintenance"], 1, 0, 1, 2)
        else:
            self.owner.centralWidget().layout().removeWidget(actions)
            self.grid.addWidget(targets, 0, 0, 2, 1)
            self.grid.addWidget(self.panels["maintenance"], 0, 1)
            self.grid.addWidget(self.panels["actions"], 1, 1)
        self.grid.setRowStretch(0, 0 if compact else 1)
        self.grid.setRowStretch(1, 1 if compact else 0)
