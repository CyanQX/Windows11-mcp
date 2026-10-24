"""Fixed manager panels; window resizing never changes their placement."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

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
            panel.setFixedHeight(150)
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
