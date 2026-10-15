"""Native movable/floating docks with explicit edge parking and durable layout state."""
from __future__ import annotations

import base64
import binascii

from PySide6.QtCore import Qt, QEvent, QPoint, QByteArray, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QIcon, QTransform
from PySide6.QtWidgets import QApplication, QDockWidget, QGridLayout, QHBoxLayout, QMainWindow, QMenu, QVBoxLayout, QWidget, QTabWidget

from installer.ui.theme import button, draw_glass, label, theme, backdrop

AREAS = {"left": Qt.DockWidgetArea.LeftDockWidgetArea, "right": Qt.DockWidgetArea.RightDockWidgetArea, "bottom": Qt.DockWidgetArea.BottomDockWidgetArea, "top": Qt.DockWidgetArea.TopDockWidgetArea}
DEFAULTS = {"targets": "left", "maintenance": "right", "actions": "bottom"}
PARK_DISTANCE = 96
PARK_THRESHOLD = 0.75


class SlidePreview(QWidget):
    """Clip a snapshot of our own dock while the native splitter keeps its minimum."""
    def __init__(self, workspace, dock, edge):
        super().__init__(workspace.canvas)
        self.dock, self.edge, self.fraction = dock, edge, 0.0
        self.image = dock.grab()
        self.setGeometry(dock.geometry())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme().colors["bg"]))
        dx = int(self.width() * self.fraction) * (-1 if self.edge == "left" else 1) if self.edge in {"left", "right"} else 0
        dy = int(self.height() * self.fraction) * (-1 if self.edge == "top" else 1) if self.edge in {"top", "bottom"} else 0
        painter.drawPixmap(QPoint(dx, dy), self.image)
        if self.fraction < 1:
            painter.setPen(QColor(theme().colors["accent"]))
            painter.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, "松开收起到边缘" if self.fraction >= PARK_THRESHOLD else "继续向边缘拖动可收起")

    def advance(self, fraction):
        self.fraction = float(fraction)
        self.update()


class EdgeHandle(QWidget):
    def __init__(self, workspace, dock, edge):
        super().__init__()
        self.workspace, self.dock, self.origin = workspace, dock, None
        self.control = button("", lambda: workspace.restore(dock), compact=True)
        angle = {"left": -90, "right": 90, "top": 0, "bottom": 180}[edge]
        self.control.setIcon(QIcon(theme().icon("chevron").pixmap(20, 20).transformed(QTransform().rotate(angle))))
        self.control.setFixedSize(34, 62) if edge in {"left", "right"} else self.control.setFixedSize(62, 30)
        self.control.setStyleSheet("QPushButton { padding: 4px; border-radius: 12px; }")
        self.control.setAccessibleName(f"恢复{dock.windowTitle()}面板，也可拖出")
        self.control.setToolTip(dock.windowTitle() + " · 点击恢复；按住并拖动可拉出面板")
        self.control.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.control)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            self.origin = event.globalPosition().toPoint()
        elif event.type() == QEvent.Type.MouseMove and self.origin and (event.globalPosition().toPoint() - self.origin).manhattanLength() > QApplication.startDragDistance():
            self.workspace.restore(self.dock)
            self.origin = None
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self.origin = None
        return False


class GlassDock(QDockWidget):
    def __init__(self, workspace, key, title, content):
        super().__init__(title, workspace.canvas)
        self.workspace, self.key = workspace, key
        self.setObjectName("dock-" + key)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        header = QWidget()
        heading = label(title, size=17, bold=True)
        heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hint = label("拖动换位", size=10, role="secondary")
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        menu = button("布局", lambda: workspace.dock_menu(self), compact=True)
        park = button("收起", lambda: workspace.collapse(self), compact=True)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 14, 16, 5)
        layout.addWidget(heading)
        layout.addStretch()
        layout.addWidget(hint)
        layout.addWidget(menu)
        layout.addWidget(park)
        header.setToolTip("拖动标题可换位、浮动或吸附；双击标题切换浮动")
        self.setTitleBarWidget(header)
        self.setWidget(content)
        self.topLevelChanged.connect(lambda floating: QTimer.singleShot(0, lambda: backdrop(self, theme().mode == "dark")))
        self.topLevelChanged.connect(lambda *_: QTimer.singleShot(0, workspace.update_empty))
        self.visibilityChanged.connect(lambda *_: QTimer.singleShot(0, workspace.update_empty))
        self.dockLocationChanged.connect(lambda area: workspace.record_area(self, area))

    def paintEvent(self, event):
        draw_glass(self)

    def closeEvent(self, event):
        if self.workspace.closing:
            event.accept()
        else:
            event.ignore()
            self.workspace.collapse(self)


class Workspace(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.closing = owner, False
        self.canvas = QMainWindow(self, Qt.WindowType.Widget)
        self.canvas.setWindowFlags(Qt.WindowType.Widget)
        self.canvas.setObjectName("dock-canvas")
        self.canvas.setStyleSheet("QMainWindow#dock-canvas { background: transparent; }")
        self.placeholder = QWidget()
        placeholder_body = QVBoxLayout(self.placeholder)
        placeholder_body.addStretch()
        title = label("工作区已腾出", size=18, bold=True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = label("面板正在浮动或收起，可通过边缘把手恢复", role="secondary", wrap=True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_body.addWidget(title)
        placeholder_body.addWidget(note)
        placeholder_body.addStretch()
        self.placeholder.setMinimumSize(200, 250)
        self.canvas.setCentralWidget(self.placeholder)
        self.placeholder.hide()
        options = QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
        if not theme().appearance["reduce_motion"]:
            options |= QMainWindow.DockOption.AnimatedDocks
        self.canvas.setDockOptions(options)
        self.canvas.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.South)
        self.docks, self.collapsed, self.last_area = {}, {}, {}
        self.rails, self.rail_layouts = {}, {}
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 0, 12, 12)
        grid.setSpacing(3)
        grid.addWidget(self.canvas, 1, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        for edge, position in (("left", (1, 0)), ("right", (1, 2)), ("top", (0, 1)), ("bottom", (2, 1))):
            rail = QWidget()
            layout = QVBoxLayout(rail) if edge in {"left", "right"} else QHBoxLayout(rail)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addStretch()
            layout.addStretch()
            grid.addWidget(rail, *position)
            rail.hide()
            self.rails[edge], self.rail_layouts[edge] = rail, layout
        self.press = None
        self.preview = None
        self.transitions = {}
        QApplication.instance().installEventFilter(self)

    def add(self, key, title, content):
        dock = GlassDock(self, key, title, content)
        self.docks[key] = dock
        self.last_area[key] = DEFAULTS[key]
        self.canvas.addDockWidget(AREAS[DEFAULTS[key]], dock)
        return dock

    def record_area(self, dock, area):
        edge = next((key for key, value in AREAS.items() if value == area), None)
        if edge:
            self.last_area[dock.key] = edge

    def collapse(self, dock, edge=None, *, animate=True):
        if dock.key in self.collapsed or dock.key in self.transitions:
            return
        edge = edge or self.last_area.get(dock.key, DEFAULTS[dock.key])
        if animate and dock.isVisible() and not dock.isFloating() and not theme().appearance["reduce_motion"]:
            preview = self.preview if self.preview and self.preview.dock == dock else SlidePreview(self, dock, edge)
            self.preview = None
            animation = QVariantAnimation(self)
            animation.setStartValue(preview.fraction)
            animation.setEndValue(1.0)
            animation.setDuration(260)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(preview.advance)
            self.transitions[dock.key] = (animation, preview, edge, True)
            animation.finished.connect(lambda: self.finish_slide(dock))
            animation.start()
        else:
            self.clear_preview()
            self.park(dock, edge)

    def finish_slide(self, dock):
        entry = self.transitions.pop(dock.key, None)
        if entry:
            animation, preview, edge, park = entry
            animation.stop()
            preview.hide()
            preview.deleteLater()
            animation.deleteLater()
            if park:
                self.park(dock, edge)

    def return_preview(self):
        preview, self.preview = self.preview, None
        if not preview:
            return
        if theme().appearance["reduce_motion"] or preview.dock.key in self.transitions:
            preview.hide()
            preview.deleteLater()
            return
        animation = QVariantAnimation(self)
        animation.setStartValue(preview.fraction)
        animation.setEndValue(0.0)
        animation.setDuration(180)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(preview.advance)
        self.transitions[preview.dock.key] = (animation, preview, preview.edge, False)
        animation.finished.connect(lambda: self.finish_slide(preview.dock))
        animation.start()

    def clear_preview(self):
        if self.preview:
            self.preview.hide()
            self.preview.deleteLater()
            self.preview = None

    def park(self, dock, edge):
        dock.hide()
        handle = EdgeHandle(self, dock, edge)
        self.collapsed[dock.key] = (edge, handle)
        layout = self.rail_layouts[edge]
        layout.insertWidget(layout.count() - 1, handle)
        self.rails[edge].show()
        self.update_empty()

    def update_empty(self):
        if self.closing:
            return
        empty = not any(dock.isVisible() and not dock.isFloating() for key, dock in self.docks.items() if key != "actions")
        changed = self.placeholder.isHidden() == empty
        self.placeholder.setVisible(empty)
        if empty and changed and "actions" in self.docks:
            self.canvas.resizeDocks([self.docks["actions"]], [150], Qt.Orientation.Vertical)

    def restore(self, dock):
        entry = self.transitions.pop(dock.key, None)
        if entry:
            animation, preview, _, _ = entry
            animation.stop()
            animation.deleteLater()
            preview.hide()
            preview.deleteLater()
        if self.preview and self.preview.dock == dock:
            self.clear_preview()
        entry = self.collapsed.pop(dock.key, None)
        if entry:
            edge, handle = entry
            handle.setParent(None)
            handle.deleteLater()
            self.rails[edge].setVisible(any(value[0] == edge for value in self.collapsed.values()))
        dock.show()
        dock.raise_()
        self.keep_on_screen(dock)
        self.update_empty()

    def keep_on_screen(self, dock):
        if not dock.isFloating():
            return
        if not any((intersection := screen.availableGeometry().intersected(dock.frameGeometry())).width() >= 80 and intersection.height() >= 50 for screen in QApplication.screens()):
            dock.move(self.owner.frameGeometry().topLeft() + QPoint(40, 70))
        bounds = dock.screen().availableGeometry()
        dock.resize(min(dock.width(), bounds.width() - 20), min(dock.height(), bounds.height() - 50))
        dock.move(max(bounds.left(), min(dock.x(), bounds.right() - dock.width())), max(bounds.top(), min(dock.y(), bounds.bottom() - dock.height() - 35)))

    def dock_menu(self, dock):
        menu = QMenu(dock)
        menu.addAction("停靠回主窗口" if dock.isFloating() else "独立浮动窗口", lambda: dock.setFloating(not dock.isFloating()))
        for edge, name in (("left", "左侧"), ("right", "右侧"), ("top", "顶部"), ("bottom", "底部")):
            menu.addAction("吸附到" + name, lambda e=edge: self.move_to(dock, e))
        menu.addSeparator()
        for key, other in self.docks.items():
            if other != dock:
                menu.addAction("与“" + other.windowTitle() + "”交换位置", lambda target=other: self.swap(dock, target))
        menu.addSeparator()
        menu.addAction("恢复默认布局", self.reset)
        menu.exec(dock.mapToGlobal(QPoint(dock.width() - 150, 45)))

    def move_to(self, dock, edge):
        self.restore(dock)
        dock.setFloating(False)
        self.canvas.addDockWidget(AREAS[edge], dock)

    def swap(self, first, second):
        a, b = self.last_area[first.key], self.last_area[second.key]
        self.move_to(first, b)
        self.move_to(second, a)

    def reset(self):
        for key, dock in self.docks.items():
            self.restore(dock)
            dock.setFloating(False)
            self.canvas.removeDockWidget(dock)
        for key, dock in self.docks.items():
            self.canvas.addDockWidget(AREAS[DEFAULTS[key]], dock)
            dock.show()
        self.canvas.resizeDocks([self.docks["targets"], self.docks["maintenance"]], [330, max(510, self.canvas.width() - 342)], Qt.Orientation.Horizontal)
        height = max(362, self.canvas.height() - 162)
        self.canvas.resizeDocks(list(self.docks.values()), [height, height, 150], Qt.Orientation.Vertical)

    def snapshot(self):
        for key in list(self.transitions):
            self.finish_slide(self.docks[key])
        return {"schema": 1, "geometry": base64.b64encode(bytes(self.owner.saveGeometry())).decode(), "state": base64.b64encode(bytes(self.canvas.saveState(1))).decode(), "collapsed": {key: entry[0] for key, entry in self.collapsed.items()}, "areas": self.last_area.copy()}

    def load(self, value):
        if not value:
            self.reset()
            return
        try:
            if value.get("schema") != 1:
                raise ValueError("unknown layout")
            if value.get("geometry"):
                self.owner.restoreGeometry(QByteArray(base64.b64decode(value["geometry"], validate=True)))
            state = base64.b64decode(value["state"], validate=True)
            if not self.canvas.restoreState(QByteArray(state), 1):
                raise ValueError("invalid dock state")
            for key, edge in value.get("areas", {}).items():
                if key in self.docks and edge in AREAS:
                    self.last_area[key] = edge
            for key, edge in value.get("collapsed", {}).items():
                if key in self.docks and edge in AREAS:
                    self.collapse(self.docks[key], edge, animate=False)
            for dock in self.docks.values():
                self.keep_on_screen(dock)
        except (ValueError, KeyError, TypeError, binascii.Error):
            self.reset()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and obj == self.canvas:
            point = event.globalPosition().toPoint()
            self.press = (point, {key: (dock.geometry(), self.last_area[key]) for key, dock in self.docks.items() if dock.isVisible() and not dock.isFloating()})
        elif kind in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease} and self.press:
            point, rects = self.press
            delta = event.globalPosition().toPoint() - point
            releasing = kind == QEvent.Type.MouseButtonRelease
            candidate = None
            for key, (rect, edge) in rects.items():
                dock = self.docks[key]
                local = self.canvas.mapFromGlobal(point)
                minimum = dock.minimumSizeHint()
                horizontal = edge in {"left", "right"}
                near = abs(local.x() - (rect.right() if edge == "left" else rect.left())) < 14 if horizontal else abs(local.y() - (rect.bottom() if edge == "top" else rect.top())) < 14
                size = dock.width() if horizontal else dock.height()
                limit = minimum.width() if horizontal else minimum.height()
                wanted = (rect.width() + (delta.x() if edge == "left" else -delta.x())) if horizontal else rect.height() + (delta.y() if edge == "top" else -delta.y())
                if near and size <= limit + 16 and wanted < limit:
                    candidate = dock
                    if not self.preview or self.preview.dock != dock:
                        self.clear_preview()
                        self.preview = SlidePreview(self, dock, edge)
                    self.preview.advance(min(0.95, (limit - wanted) / PARK_DISTANCE))
                    if releasing and self.preview.fraction >= PARK_THRESHOLD:
                        self.collapse(dock, edge)
                    break
            if releasing and self.preview:
                self.return_preview()
            elif candidate is None:
                self.clear_preview()
            if releasing:
                self.press = None
        # Native docking handles drag previews/tabbing. A floating title released at
        # an outer edge also snaps without requiring a second menu action.
        if kind == QEvent.Type.NonClientAreaMouseButtonRelease:
            for dock in self.docks.values():
                if dock.isFloating() and dock.isVisible():
                    if hasattr(event, "globalPosition"):
                        local = self.canvas.mapFromGlobal(event.globalPosition().toPoint())
                        bounds = self.canvas.rect().adjusted(-18, -18, 18, 18)
                        if bounds.contains(local):
                            distances = {"left": abs(local.x()), "right": abs(self.canvas.width() - local.x()), "top": abs(local.y()), "bottom": abs(self.canvas.height() - local.y())}
                            edge = min(distances, key=distances.get)
                            if distances[edge] < 40:
                                self.move_to(dock, edge)
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(theme().colors["end"]))
        gradient.setColorAt(1, QColor(theme().colors["bg"]))
        painter.fillRect(self.rect(), gradient)
