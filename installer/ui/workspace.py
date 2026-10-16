"""Native movable/floating docks with explicit edge parking and durable layout state."""
from __future__ import annotations

import base64
import binascii

from dataclasses import dataclass

from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QRectF, QSize, QByteArray, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QIcon, QTransform, QPen, QMouseEvent
from PySide6.QtWidgets import QApplication, QDockWidget, QGridLayout, QHBoxLayout, QMainWindow, QMenu, QVBoxLayout, QWidget, QTabWidget

from installer.ui.theme import button, draw_glass, label, theme, backdrop

AREAS = {"left": Qt.DockWidgetArea.LeftDockWidgetArea, "right": Qt.DockWidgetArea.RightDockWidgetArea, "bottom": Qt.DockWidgetArea.BottomDockWidgetArea, "top": Qt.DockWidgetArea.TopDockWidgetArea}
DEFAULTS = {"targets": "left", "maintenance": "right", "actions": "bottom"}
PARK_THRESHOLD = 0.4


class SlidePreview(QWidget):
    """Move the complete rendered panel and release its real dock space together.

    Live controls stay intact off-layout during the short gesture. The transparent
    viewport replaces both the title and body, so their minimum sizes cannot keep
    an empty footprint or leave the original glass border behind.
    """
    def __init__(self, workspace, dock, edge):
        super().__init__(dock)
        self.workspace, self.dock, self.edge, self.fraction = workspace, dock, edge, 0.0
        self.image = dock.grab()
        self.floating_rect = QRect(dock.geometry()) if dock.isFloating() else None
        self.expanded = dock.size()
        self.original_rect = dock.geometry()
        self.horizontal = edge in {"left", "right"}
        self.shared_span = bool(workspace.aligned_docks(dock, self.horizontal)) if not dock.isFloating() else False
        self.extent = self.expanded.width() if self.horizontal else self.expanded.height()
        self.minimum, self.maximum = dock.minimumSize(), dock.maximumSize()
        self.body, self.title = dock.widget(), dock.titleBarWidget()
        dock.slide = self
        if self.shared_span:
            # Keep native widgets and their layout hints in a shared column.
            # Replacing one row with a zero-minimum placeholder lets Qt remove
            # that row from the layout while its wider sibling is still present.
            self.setGeometry(dock.rect())
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.show()
            self.raise_()
            return
        self.body.hide()
        self.title.hide()
        self.blank_title = QWidget()
        self.blank_title.setFixedSize(0, 0)
        self.blank_body = QWidget()
        self.blank_body.setMinimumSize(1, 1)
        self.blank_body.setSizePolicy(self.body.sizePolicy())
        dock.setTitleBarWidget(self.blank_title)
        dock.setWidget(self.blank_body)
        dock.setMinimumSize(0 if self.horizontal else self.minimum.width(), self.minimum.height() if self.horizontal else 0)
        self.setGeometry(dock.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.shared_span:
            painter.fillRect(self.rect(), QColor(theme().colors["bg"]))
        painter.setOpacity(1 - self.fraction)
        # The right/bottom edge of the viewport already moves toward its edge as
        # the layout shrinks; start edges need an additional negative translation.
        dx = -round(self.extent * self.fraction) if self.edge == "left" else 0
        dy = -round(self.extent * self.fraction) if self.edge == "top" else 0
        scale = 1 - .04 * self.fraction
        painter.translate(dx, dy)
        painter.translate(self.expanded.width() / 2, self.expanded.height() / 2)
        painter.scale(scale, scale)
        painter.translate(-self.expanded.width() / 2, -self.expanded.height() / 2)
        painter.drawPixmap(QPoint(), self.image)

    def advance(self, fraction):
        self.fraction = max(0., min(1., float(fraction)))
        if self.shared_span:
            # The sibling still owns this column/row. Shrinking one native slot
            # against that sibling's minimum corrupts Qt's nested dock layout.
            # Slide the complete image inside its stable slot, then hide it.
            self.setGeometry(self.dock.rect())
            self.update()
            return
        size = max(1, round(self.extent * (1 - self.fraction)))
        if self.horizontal:
            self.dock.setFixedWidth(size)
        else:
            self.dock.setFixedHeight(size)
        if self.floating_rect is not None:
            rect = QRect(self.floating_rect)
            if self.horizontal:
                rect.setWidth(size)
                if self.edge == "right":
                    rect.moveRight(self.floating_rect.right())
            else:
                rect.setHeight(size)
                if self.edge == "bottom":
                    rect.moveBottom(self.floating_rect.bottom())
            self.dock.setGeometry(rect)
            self.setGeometry(self.dock.rect())
            self.update()
            return
        peers, sizes = [self.dock], [size]
        for other in self.workspace.docks.values():
            if other is self.dock or other.isFloating() or not other.isVisible():
                continue
            rect = other.geometry()
            overlap = min(rect.bottom(), self.original_rect.bottom()) - max(rect.top(), self.original_rect.top()) if self.horizontal else min(rect.right(), self.original_rect.right()) - max(rect.left(), self.original_rect.left())
            if overlap > 0:
                peers.append(other)
                sizes.append((other.width() if self.horizontal else other.height()) + max(0, self.extent - size))
        self.workspace.canvas.resizeDocks(peers, sizes, Qt.Orientation.Horizontal if self.horizontal else Qt.Orientation.Vertical)
        self.workspace.canvas.layout().activate()
        self.setGeometry(self.dock.rect())
        self.update()

    def dispose(self):
        self.hide()
        if self.shared_span:
            self.dock.slide = None
            self.dock.update()
            self.deleteLater()
            return
        self.dock.setMaximumSize(self.maximum)
        self.dock.setMinimumSize(self.minimum)
        self.dock.setTitleBarWidget(self.title)
        self.dock.setWidget(self.body)
        self.title.show()
        self.body.show()
        self.dock.slide = None
        self.blank_title.deleteLater()
        self.blank_body.deleteLater()
        self.deleteLater()


@dataclass(frozen=True)
class DropTarget:
    rect: QRect
    edge: str
    key: str | None = None
    tab: bool = False


class DockDropPreview(QWidget):
    """A non-interactive outline of the exact destination used on release."""
    def __init__(self, canvas):
        super().__init__(canvas)
        self.target = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def present(self, target):
        self.target = target
        if target is None:
            self.hide()
            return
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        if self.target is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme().colors["accent"])
        fill = QColor(color)
        fill.setAlpha(28)
        painter.setBrush(fill)
        painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(QRectF(self.target.rect).adjusted(3, 3, -3, -3), 17, 17)


class DockTitle(QWidget):
    """Explicit title dragging gives floating panels a reliable landing preview."""
    def __init__(self, workspace, dock):
        super().__init__()
        self.workspace, self.dock = workspace, dock
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.start_title_drag(self.dock, event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.workspace.drag_title(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.finish_title_drag(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.cancel_title_drag()
            self.dock.setFloating(not self.dock.isFloating())
            event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.workspace.cancel_title_drag()
        else:
            super().keyPressEvent(event)


class EdgeHandle(QWidget):
    def __init__(self, workspace, dock, edge):
        super().__init__()
        self.workspace, self.dock, self.edge, self.origin = workspace, dock, edge, None
        self.moving = False
        self.control = button("", lambda: workspace.restore(dock, animate=True), compact=True)
        angle = {"left": -90, "right": 90, "top": 0, "bottom": 180}[edge]
        self.control.setIcon(QIcon(theme().icon("chevron").pixmap(20, 20).transformed(QTransform().rotate(angle))))
        self.control.setFixedSize(34, 62) if edge in {"left", "right"} else self.control.setFixedSize(62, 30)
        self.control.setStyleSheet("QPushButton { padding: 4px; border-radius: 12px; }")
        self.control.setAccessibleName(f"恢复{dock.windowTitle()}面板，也可拖出")
        self.control.setToolTip(dock.windowTitle())
        self.control.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.control)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            self.origin = event.globalPosition().toPoint()
            self.moving = False
        elif event.type() == QEvent.Type.MouseMove and self.origin:
            delta = event.globalPosition().toPoint() - self.origin
            pull = delta.x() if self.edge == "left" else -delta.x() if self.edge == "right" else delta.y() if self.edge == "top" else -delta.y()
            if self.moving or pull > QApplication.startDragDistance():
                self.moving = True
                self.workspace.pull_from_edge(self.dock, max(0, pull))
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self.origin = None
            if self.moving:
                self.moving = False
                self.control.setDown(False)
                self.workspace.finish_reveal(self.dock)
                return True
        return False


class GlassDock(QDockWidget):
    def __init__(self, workspace, key, title, content):
        super().__init__(title, workspace.canvas)
        self.workspace, self.key, self.slide = workspace, key, None
        self.setObjectName("dock-" + key)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        header = DockTitle(workspace, self)
        heading = label(title, size=17, bold=True)
        heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 14, 16, 5)
        layout.addWidget(heading)
        layout.addStretch()
        header.setAccessibleName(title)
        header.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda point: workspace.dock_menu(self, header.mapToGlobal(point)))
        self.setTitleBarWidget(header)
        self.setWidget(content)
        self.topLevelChanged.connect(lambda floating: QTimer.singleShot(0, lambda: backdrop(self, theme().mode == "dark")))
        self.topLevelChanged.connect(lambda *_: QTimer.singleShot(0, workspace.update_empty))
        self.visibilityChanged.connect(lambda *_: QTimer.singleShot(0, workspace.update_empty))
        self.dockLocationChanged.connect(lambda area: workspace.record_area(self, area))
        self.topLevelChanged.connect(lambda *_: workspace.floating_changed(self))
        self.topLevelChanged.connect(lambda *_: workspace.release_footer_limit())

    def paintEvent(self, event):
        if self.slide is None:
            draw_glass(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.slide:
            self.slide.setGeometry(self.rect())

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
        placeholder_body.addWidget(title)
        placeholder_body.addStretch()
        self.placeholder.setMinimumSize(200, 250)
        self.canvas.setCentralWidget(self.placeholder)
        self.placeholder.hide()
        options = QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
        self.canvas.setDockOptions(options)
        self.canvas.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.South)
        self.docks, self.collapsed, self.last_area, self.park_sizes = {}, {}, {}, {}
        self.park_float_rects = {}
        self.rails, self.rail_layouts = {}, {}
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 0, 12, 12)
        grid.setSpacing(3)
        grid.addWidget(self.canvas, 1, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        for edge, position in (("left", (1, 0)), ("right", (1, 2)), ("top", (0, 1)), ("bottom", (2, 1))):
            rail = QWidget(self)
            layout = QVBoxLayout(rail) if edge in {"left", "right"} else QHBoxLayout(rail)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addStretch()
            layout.addStretch()
            rail.hide()
            self.rails[edge], self.rail_layouts[edge] = rail, layout
        self.press = None
        self.split_owned, self.native_event = False, False
        self.split_key, self.split_live = None, None
        self.floating_resize = None
        self.preview = None
        self.transitions = {}
        self.title_drag = None
        self.drop_preview = DockDropPreview(self.canvas)
        self.footer_height, self.maximized_footer_height = 150, None
        self.normal_footer_width = 560
        self.footer_maximum = None
        self.normalize_timer = QTimer(self)
        self.normalize_timer.setSingleShot(True)
        self.normalize_timer.timeout.connect(self.normalize_layout)
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
            self.schedule_normalize()

    def schedule_normalize(self):
        if hasattr(self, "normalize_timer") and not self.closing:
            self.normalize_timer.start(0)

    def release_footer_limit(self):
        if self.footer_maximum is not None:
            self.docks["actions"].setMaximumHeight(self.footer_maximum)
            self.footer_maximum = None

    def release_live_split(self):
        if self.split_live:
            dock, minimum, maximum = self.split_live
            dock.setMaximumSize(maximum)
            dock.setMinimumSize(minimum)
            self.split_live = None

    def floating_changed(self, dock):
        self.cancel_divider_gesture()
        self.release_footer_limit()
        if dock.key == "actions" and dock.isFloating():
            QTimer.singleShot(0, self.compact_floating_actions)
        self.schedule_normalize()

    def compact_floating_actions(self):
        dock = self.docks.get("actions")
        if not dock or not dock.isVisible() or not dock.isFloating() or dock.slide or self.floating_resize or self.title_drag:
            return
        minimum = dock.minimumSizeHint()
        dock.setMinimumHeight(minimum.height())
        dock.resize(max(minimum.width(), min(dock.width(), self.normal_footer_width)), max(minimum.height(), self.footer_height))
        self.keep_on_screen(dock)

    def cancel_divider_gesture(self):
        press, self.press = self.press, None
        floating, self.floating_resize = self.floating_resize, None
        if press and not self.split_owned:
            self.native_event = True
            try:
                local = self.canvas.mapFromGlobal(press[0])
                QApplication.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, local, press[0], Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
            finally:
                self.native_event = False
        self.split_owned, self.split_key = False, None
        self.clear_preview()
        self.release_live_split()
        if floating:
            floating["dock"].setGeometry(floating["rect"])
        if QWidget.mouseGrabber() is self.canvas:
            self.canvas.releaseMouse()

    def adjacent_edges(self, dock):
        """Actual neighbors determine the resize axis, including nested docks."""
        rect = dock.geometry()
        neighbors = [other.geometry() for other in self.docks.values() if other is not dock and other.isVisible() and not other.isFloating()]
        if self.placeholder.isVisible():
            neighbors.append(self.placeholder.geometry())
        edges = set()
        for other in neighbors:
            overlap_y = min(rect.bottom(), other.bottom()) - max(rect.top(), other.top())
            overlap_x = min(rect.right(), other.right()) - max(rect.left(), other.left())
            if overlap_y > 0:
                if 0 < other.left() - rect.right() <= 16:
                    edges.add("left")
                if 0 < rect.left() - other.right() <= 16:
                    edges.add("right")
            if overlap_x > 0:
                if 0 < other.top() - rect.bottom() <= 16:
                    edges.add("top")
                if 0 < rect.top() - other.bottom() <= 16:
                    edges.add("bottom")
        return edges

    def divider_snapshot(self, point):
        local = self.canvas.mapFromGlobal(point)
        result = {}
        for key, dock in self.docks.items():
            if not dock.isVisible() or dock.isFloating() or dock.slide:
                continue
            rect = dock.geometry()
            distances = {"left": abs(local.x() - rect.right()), "right": abs(local.x() - rect.left()), "top": abs(local.y() - rect.bottom()), "bottom": abs(local.y() - rect.top())}
            candidates = [edge for edge in self.adjacent_edges(dock) if distances[edge] < 14 and (rect.top() <= local.y() <= rect.bottom() if edge in {"left", "right"} else rect.left() <= local.x() <= rect.right())]
            if candidates:
                edge = min(candidates, key=lambda item: distances[item])
                result[key] = (rect, edge, self.divider_minimum(dock, edge))
        return result

    def divider_minimum(self, dock, edge):
        """A native separator stops at the minimum of its entire shared span.

        Vertically stacked docks share their column width, so a wider toolbar
        can stop resizing before the panel under the pointer reaches its own
        minimum. Use that real stop to begin sliding only the pointed panel.
        """
        minimum = QSize(dock.minimumSizeHint())
        horizontal = edge in {"left", "right"}
        for other in self.aligned_docks(dock, horizontal):
            hint = other.minimumSizeHint()
            if horizontal:
                minimum.setWidth(max(minimum.width(), hint.width()))
            else:
                minimum.setHeight(max(minimum.height(), hint.height()))
        return minimum

    def aligned_docks(self, dock, horizontal):
        rect = dock.geometry()
        peers = []
        for other in self.docks.values():
            if other is dock or not other.isVisible() or other.isFloating():
                continue
            bounds = other.geometry()
            aligned = abs(rect.left() - bounds.left()) <= 2 and abs(rect.right() - bounds.right()) <= 2 if horizontal else abs(rect.top() - bounds.top()) <= 2 and abs(rect.bottom() - bounds.bottom()) <= 2
            if aligned:
                peers.append(other)
        return peers

    def footer_edge(self, dock):
        edges = self.adjacent_edges(dock)
        if "bottom" in edges:
            return "bottom"
        if "top" in edges:
            return "top"
        return self.last_area.get(dock.key, DEFAULTS[dock.key])

    def floating_resize_edge(self, obj, point):
        dock = self.docks.get("actions")
        if not dock or not dock.isFloating() or not dock.isVisible() or dock.slide or not isinstance(obj, QWidget) or not (obj is dock or dock.isAncestorOf(obj)):
            return None
        local = dock.mapFromGlobal(point)
        if -6 <= local.x() <= dock.width() + 6:
            if -6 <= local.y() <= 6:
                return "top"
            if dock.height() - 6 <= local.y() <= dock.height() + 6:
                return "bottom"
        return None

    def resize_floating_actions(self, point, *, releasing=False):
        drag = self.floating_resize
        dock, rect, minimum = drag["dock"], drag["rect"], drag["minimum"]
        delta = point.y() - drag["point"].y()
        wanted = rect.height() + (delta if drag["edge"] == "bottom" else -delta)
        if wanted >= minimum:
            self.clear_preview()
            bounds = QRect(rect)
            bounds.setHeight(min(wanted, dock.screen().availableGeometry().height() - 40))
            if drag["edge"] == "top":
                bounds.moveBottom(rect.bottom())
            dock.setGeometry(bounds)
        else:
            if not self.preview:
                bounds = QRect(rect)
                bounds.setHeight(minimum)
                if drag["edge"] == "top":
                    bounds.moveBottom(rect.bottom())
                dock.setGeometry(bounds)
                self.preview = SlidePreview(self, dock, "bottom" if drag["edge"] == "top" else "top")
            self.preview.advance(min(.99, (minimum - wanted) / max(1, minimum)))
        if releasing:
            self.floating_resize = None
            if self.preview:
                if self.preview.fraction > PARK_THRESHOLD:
                    self.collapse(dock, self.preview.edge)
                else:
                    self.return_preview()
            if QWidget.mouseGrabber() is self.canvas:
                self.canvas.releaseMouse()

    def resize_owned_split(self, dock, edge, wanted, minimum):
        if self.split_live is None:
            self.split_live = (dock, dock.minimumSize(), dock.maximumSize())
        horizontal = edge in {"left", "right"}
        extent = dock.width() if horizontal else dock.height()
        maximum = self.canvas.width() if horizontal else self.canvas.height()
        rect = dock.geometry()
        neighbors = []
        for other in self.docks.values():
            if other is dock or other.isFloating() or not other.isVisible():
                continue
            other_rect = other.geometry()
            overlap = min(rect.bottom(), other_rect.bottom()) - max(rect.top(), other_rect.top()) if horizontal else min(rect.right(), other_rect.right()) - max(rect.left(), other_rect.left())
            if overlap > 0:
                length = other.width() if horizontal else other.height()
                limit = other.minimumSizeHint().width() if horizontal else other.minimumSizeHint().height()
                neighbors.append((other, length))
                maximum = min(maximum, extent + max(0, length - limit))
        value = max(minimum, min(maximum, round(wanted)))
        peers = [dock, *(other for other, _ in neighbors)]
        sizes = [value, *(max(1, length + extent - value) for _, length in neighbors)]
        if horizontal:
            dock.setFixedWidth(value)
        else:
            dock.setFixedHeight(value)
        self.canvas.resizeDocks(peers, sizes, Qt.Orientation.Horizontal if horizontal else Qt.Orientation.Vertical)
        self.canvas.layout().activate()

    def normalize_layout(self):
        # Normal and maximized windows have separate toolbar heights. Nested
        # split geometry, rather than QMainWindow's area name, determines its axis.
        if self.closing or self.press or self.title_drag or self.preview or self.transitions:
            return
        dock = self.docks.get("actions")
        if not dock or not dock.isVisible() or dock.isFloating():
            self.release_footer_limit()
            return
        minimum = dock.minimumSizeHint().height()
        preferred = self.maximized_footer_height if self.owner.isMaximized() and self.maximized_footer_height is not None else self.footer_height
        desired = preferred
        maximum = max(minimum, round(self.canvas.height() * .42))
        desired = max(minimum, min(maximum, desired))
        if self.placeholder.isVisible() or self.footer_edge(dock) in {"left", "right"}:
            if self.footer_maximum is None:
                self.footer_maximum = dock.maximumHeight()
            dock.setMaximumHeight(desired)
        else:
            self.release_footer_limit()
        if abs(dock.height() - desired) > 2:
            peers = [other for other in self.docks.values() if other is not dock and other.isVisible() and not other.isFloating()]
            self.canvas.resizeDocks([*peers, dock], [max(1, self.canvas.height() - desired - 12)] * len(peers) + [desired], Qt.Orientation.Vertical)
        if not self.owner.isMaximized() and not self.owner.isMinimized():
            self.normal_footer_width = dock.width()

    def start_title_drag(self, dock, point):
        if self.preview or self.transitions or dock.key in self.collapsed:
            return
        self.title_drag = {"dock": dock, "point": point, "offset": point - dock.mapToGlobal(QPoint()), "size": dock.size(), "edge": self.last_area[dock.key], "floating": dock.isFloating(), "moved": False}
        dock.titleBarWidget().setFocus(Qt.FocusReason.MouseFocusReason)

    def drag_title(self, point):
        drag = self.title_drag
        if not drag:
            return
        dock = drag["dock"]
        if not drag["moved"]:
            if (point - drag["point"]).manhattanLength() < QApplication.startDragDistance():
                return
            drag["moved"] = True
            if not dock.isFloating():
                dock.setFloating(True)
                dock.resize(drag["size"])
            dock.titleBarWidget().grabMouse()
        dock.move(point - drag["offset"])
        self.drop_preview.present(self.drop_target(dock, point))

    def drop_target(self, dock, global_point):
        point = self.canvas.mapFromGlobal(global_point)
        bounds = self.canvas.rect()
        if not bounds.adjusted(-18, -18, 18, 18).contains(point):
            return None
        distances = {"left": abs(point.x()), "right": abs(bounds.right() - point.x()), "top": abs(point.y()), "bottom": abs(bounds.bottom() - point.y())}
        edge = min(distances, key=distances.get)
        if distances[edge] <= 36:
            rect = QRect(bounds)
            if edge in {"left", "right"}:
                rect.setWidth(min(max(dock.minimumSizeHint().width(), bounds.width() // 3), bounds.width() // 2))
                if edge == "right":
                    rect.moveRight(bounds.right())
            else:
                rect.setHeight(min(max(dock.minimumSizeHint().height(), 150), bounds.height() // 3))
                if edge == "bottom":
                    rect.moveBottom(bounds.bottom())
            return DropTarget(rect, edge)
        for other in self.docks.values():
            if other is dock or other.isFloating() or not other.isVisible():
                continue
            rect = QRect(other.geometry())
            if not rect.contains(point):
                continue
            rx = (point.x() - rect.left()) / max(1, rect.width())
            ry = (point.y() - rect.top()) / max(1, rect.height())
            choices = {"left": rx, "right": 1 - rx, "top": ry, "bottom": 1 - ry}
            edge = min(choices, key=choices.get)
            if choices[edge] >= .25:
                return DropTarget(rect, self.last_area[other.key], other.key, True)
            horizontal = edge in {"left", "right"}
            required = dock.minimumSizeHint().width() + other.minimumSizeHint().width() + 12 if horizontal else dock.minimumSizeHint().height() + other.minimumSizeHint().height() + 12
            if required > (rect.width() if horizontal else rect.height()):
                return DropTarget(rect, self.last_area[other.key], other.key, True)
            if edge in {"left", "right"}:
                rect.setWidth(rect.width() // 2)
                if edge == "right":
                    rect.moveRight(other.geometry().right())
            else:
                rect.setHeight(rect.height() // 2)
                if edge == "bottom":
                    rect.moveBottom(other.geometry().bottom())
            return DropTarget(rect, edge, other.key)
        return DropTarget(bounds, self.last_area[dock.key])

    def finish_title_drag(self, point):
        drag, self.title_drag = self.title_drag, None
        self.drop_preview.present(None)
        if not drag or not drag["moved"]:
            return
        dock = drag["dock"]
        if QWidget.mouseGrabber() is dock.titleBarWidget():
            dock.titleBarWidget().releaseMouse()
        target = self.drop_target(dock, point)
        if target:
            dock.setFloating(False)
            if target.key:
                other = self.docks[target.key]
                if target.tab:
                    self.canvas.tabifyDockWidget(other, dock)
                else:
                    orientation = Qt.Orientation.Horizontal if target.edge in {"left", "right"} else Qt.Orientation.Vertical
                    self.canvas.splitDockWidget(other, dock, orientation)
                    if target.edge in {"left", "top"}:
                        self.canvas.splitDockWidget(dock, other, orientation)
            else:
                self.canvas.addDockWidget(AREAS[target.edge], dock)
            dock.show()
            dock.raise_()
        else:
            self.keep_on_screen(dock)
            if dock.key == "actions":
                self.compact_floating_actions()
        self.update_empty()
        self.schedule_normalize()

    def cancel_title_drag(self):
        drag, self.title_drag = self.title_drag, None
        self.drop_preview.present(None)
        if drag and QWidget.mouseGrabber() is drag["dock"].titleBarWidget():
            drag["dock"].titleBarWidget().releaseMouse()
        if drag and drag["moved"] and not drag["floating"]:
            self.move_to(drag["dock"], drag["edge"])
        self.schedule_normalize()

    def collapse(self, dock, edge=None, *, animate=True):
        if (dock.key in self.collapsed and not (self.preview and self.preview.dock == dock)) or dock.key in self.transitions:
            return
        edge = edge or (self.footer_edge(dock) if dock.key == "actions" else self.last_area.get(dock.key, DEFAULTS[dock.key]))
        if animate and dock.isVisible() and not theme().appearance["reduce_motion"]:
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
            if park:
                if preview.floating_rect is not None:
                    self.park_float_rects[dock.key] = QRect(preview.floating_rect)
                dock.hide()
            else:
                preview.advance(0)
            preview.dispose()
            animation.deleteLater()
            if park:
                self.park(dock, edge, preview.expanded)
            self.schedule_normalize()

    def return_preview(self):
        preview, self.preview = self.preview, None
        if not preview:
            return
        if theme().appearance["reduce_motion"] or preview.dock.key in self.transitions:
            preview.advance(0)
            preview.dispose()
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
            dock = self.preview.dock
            self.preview.advance(0)
            self.preview.dispose()
            self.preview = None
            if dock.key in self.collapsed:
                dock.hide()

    def park(self, dock, edge, expanded=None):
        if dock.isFloating() and expanded is None:
            self.park_float_rects[dock.key] = QRect(dock.geometry())
        self.park_sizes[dock.key] = QSize(expanded or dock.size())
        dock.hide()
        if dock.key in self.collapsed:
            self.update_empty()
            return
        handle = EdgeHandle(self, dock, edge)
        self.collapsed[dock.key] = (edge, handle)
        layout = self.rail_layouts[edge]
        layout.insertWidget(layout.count() - 1, handle)
        self.rails[edge].show()
        self.position_rails()
        self.update_empty()

    def update_empty(self):
        if self.closing:
            return
        empty = not any(dock.isVisible() and not dock.isFloating() for key, dock in self.docks.items() if key != "actions")
        changed = self.placeholder.isHidden() == empty
        self.placeholder.setVisible(empty)
        if empty and changed and "actions" in self.docks:
            self.canvas.resizeDocks([self.docks["actions"]], [150], Qt.Orientation.Vertical)
        self.schedule_normalize()

    def remove_handle(self, dock):
        entry = self.collapsed.pop(dock.key, None)
        if entry:
            edge, handle = entry
            handle.setParent(None)
            handle.deleteLater()
            self.rails[edge].setVisible(any(value[0] == edge for value in self.collapsed.values()))

    def pull_from_edge(self, dock, pull):
        if dock.key in self.transitions:
            return
        if not self.preview:
            entry = self.collapsed.get(dock.key)
            if not entry:
                return
            if dock.isFloating():
                self.restore(dock)
                return
            edge = entry[0]
            dock.show()
            size = self.park_sizes.get(dock.key, dock.size())
            self.canvas.resizeDocks([dock], [size.width() if edge in {"left", "right"} else size.height()], Qt.Orientation.Horizontal if edge in {"left", "right"} else Qt.Orientation.Vertical)
            self.canvas.layout().activate()
            self.preview = SlidePreview(self, dock, edge)
        if self.preview.dock is dock:
            self.preview.advance(1 - min(1, pull / max(1, self.preview.extent)))

    def finish_reveal(self, dock):
        if not self.preview or self.preview.dock is not dock:
            return
        if self.preview.fraction <= PARK_THRESHOLD:
            self.remove_handle(dock)
            self.return_preview()
        else:
            self.collapse(dock, self.preview.edge)

    def restore(self, dock, *, animate=False):
        entry = self.transitions.pop(dock.key, None)
        if entry:
            animation, preview, _, _ = entry
            animation.stop()
            animation.deleteLater()
            preview.advance(0)
            preview.dispose()
        if self.preview and self.preview.dock == dock:
            self.clear_preview()
        was_parked = dock.key in self.collapsed
        edge = self.collapsed[dock.key][0] if was_parked else self.last_area[dock.key]
        self.remove_handle(dock)
        dock.show()
        dock.raise_()
        if was_parked and not dock.isFloating() and dock.key in self.park_sizes:
            size = self.park_sizes[dock.key]
            self.canvas.resizeDocks([dock], [size.width()], Qt.Orientation.Horizontal)
            self.canvas.resizeDocks([dock], [size.height()], Qt.Orientation.Vertical)
            self.canvas.layout().activate()
        if dock.isFloating() and dock.key in self.park_float_rects:
            dock.setGeometry(self.park_float_rects[dock.key])
        self.keep_on_screen(dock)
        if was_parked and animate and not dock.isFloating() and not theme().appearance["reduce_motion"]:
            self.canvas.layout().activate()
            self.preview = SlidePreview(self, dock, edge)
            self.preview.advance(1)
            self.return_preview()
        self.update_empty()
        self.schedule_normalize()

    def position_rails(self):
        rect = self.canvas.geometry()
        for edge, rail in self.rails.items():
            if edge in {"left", "right"}:
                width = max(38, rail.minimumSizeHint().width())
                rail.setGeometry(rect.left() if edge == "left" else rect.right() - width + 1, rect.top(), width, rect.height())
            else:
                height = max(34, rail.minimumSizeHint().height())
                rail.setGeometry(rect.left(), rect.top() if edge == "top" else rect.bottom() - height + 1, rect.width(), height)
            rail.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_rails()
        self.schedule_normalize()

    def keep_on_screen(self, dock):
        if not dock.isFloating():
            return
        if not any((intersection := screen.availableGeometry().intersected(dock.frameGeometry())).width() >= 80 and intersection.height() >= 50 for screen in QApplication.screens()):
            dock.move(self.owner.frameGeometry().topLeft() + QPoint(40, 70))
        bounds = dock.screen().availableGeometry()
        dock.resize(min(dock.width(), bounds.width() - 20), min(dock.height(), bounds.height() - 50))
        dock.move(max(bounds.left(), min(dock.x(), bounds.right() - dock.width())), max(bounds.top(), min(dock.y(), bounds.bottom() - dock.height() - 35)))

    def dock_menu(self, dock, position=None):
        menu = QMenu(dock)
        menu.addAction("收起面板", lambda: self.collapse(dock))
        menu.addAction("停靠回主窗口" if dock.isFloating() else "独立浮动窗口", lambda: dock.setFloating(not dock.isFloating()))
        for edge, name in (("left", "左侧"), ("right", "右侧"), ("top", "顶部"), ("bottom", "底部")):
            menu.addAction("吸附到" + name, lambda e=edge: self.move_to(dock, e))
        menu.addSeparator()
        for key, other in self.docks.items():
            if other != dock:
                menu.addAction("与“" + other.windowTitle() + "”交换位置", lambda target=other: self.swap(dock, target))
        menu.addSeparator()
        menu.addAction("恢复默认布局", self.reset)
        try:
            menu.exec(position if position is not None else dock.mapToGlobal(QPoint(dock.width() - 150, 45)))
        finally:
            menu.deleteLater()

    def move_to(self, dock, edge):
        self.restore(dock)
        dock.setFloating(False)
        self.canvas.addDockWidget(AREAS[edge], dock)
        self.schedule_normalize()

    def swap(self, first, second):
        a, b = self.last_area[first.key], self.last_area[second.key]
        self.move_to(first, b)
        self.move_to(second, a)

    def reset(self):
        self.cancel_title_drag()
        self.cancel_divider_gesture()
        self.release_footer_limit()
        self.footer_height, self.maximized_footer_height = 150, None
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
        self.update_empty()
        self.schedule_normalize()

    def snapshot(self):
        self.cancel_divider_gesture()
        self.cancel_title_drag()
        for key in list(self.transitions):
            self.finish_slide(self.docks[key])
        return {"schema": 1, "geometry": base64.b64encode(bytes(self.owner.saveGeometry())).decode(), "state": base64.b64encode(bytes(self.canvas.saveState(1))).decode(), "collapsed": {key: entry[0] for key, entry in self.collapsed.items()}, "areas": self.last_area.copy(), "footer_height": self.footer_height, "maximized_footer_height": self.maximized_footer_height}

    def load(self, value):
        if not value:
            self.reset()
            return
        try:
            if value.get("schema") != 1:
                raise ValueError("unknown layout")
            height = value.get("footer_height")
            maximized = value.get("maximized_footer_height")
            self.footer_height = height if isinstance(height, int) and 80 <= height <= 1000 else 150
            self.maximized_footer_height = maximized if isinstance(maximized, int) and 80 <= maximized <= 1500 else None
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
            self.schedule_normalize()
        except (ValueError, KeyError, TypeError, binascii.Error):
            self.reset()

    def eventFilter(self, obj, event):
        if self.native_event:
            return False
        kind = event.type()
        if obj is self.owner and kind == QEvent.Type.WindowStateChange:
            self.cancel_divider_gesture()
            for key in list(self.transitions):
                self.finish_slide(self.docks[key])
            QTimer.singleShot(0, self.compact_floating_actions)
            self.schedule_normalize()
        if (obj is self.canvas and kind == QEvent.Type.UngrabMouse or obj is self.owner and kind == QEvent.Type.WindowDeactivate) and (self.press or self.floating_resize):
            self.cancel_divider_gesture()
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape and (self.press or self.floating_resize):
            self.cancel_divider_gesture()
            return True
        if kind in {QEvent.Type.MouseButtonPress, QEvent.Type.NonClientAreaMouseButtonPress} and event.button() == Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint()
            edge = self.floating_resize_edge(obj, point)
            if edge:
                self.cancel_divider_gesture()
                dock = self.docks["actions"]
                self.floating_resize = {"dock": dock, "edge": edge, "point": point, "rect": QRect(dock.geometry()), "minimum": dock.minimumSizeHint().height()}
                self.canvas.grabMouse()
                return True
        if self.floating_resize and kind in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease, QEvent.Type.NonClientAreaMouseMove, QEvent.Type.NonClientAreaMouseButtonRelease}:
            self.resize_floating_actions(event.globalPosition().toPoint(), releasing=kind in {QEvent.Type.MouseButtonRelease, QEvent.Type.NonClientAreaMouseButtonRelease})
            return True
        if obj == self.canvas and kind == QEvent.Type.Resize:
            self.position_rails()
            self.schedule_normalize()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and obj == self.canvas:
            self.release_footer_limit()
            point = event.globalPosition().toPoint()
            self.press = (point, self.divider_snapshot(point))
        elif kind in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease} and self.press:
            point, rects = self.press
            delta = event.globalPosition().toPoint() - point
            releasing = kind == QEvent.Type.MouseButtonRelease
            candidate = None
            for key, (rect, edge, minimum) in rects.items():
                dock = self.docks[key]
                local = self.canvas.mapFromGlobal(point)
                horizontal = edge in {"left", "right"}
                near = abs(local.x() - (rect.right() if edge == "left" else rect.left())) < 14 if horizontal else abs(local.y() - (rect.bottom() if edge == "top" else rect.top())) < 14
                size = dock.width() if horizontal else dock.height()
                limit = minimum.width() if horizontal else minimum.height()
                wanted = (rect.width() + (delta.x() if edge == "left" else -delta.x())) if horizontal else rect.height() + (delta.y() if edge == "top" else -delta.y())
                if near and (size <= limit + 16 or self.split_owned and self.split_key == key) and wanted < limit:
                    candidate = dock
                    if not self.preview or self.preview.dock != dock:
                        self.clear_preview()
                        self.release_live_split()
                        # End Qt's cached separator gesture before changing the
                        # layout ourselves. Keeping it active leaves its old slot
                        # behind even when the QWidget has already become narrow.
                        if not self.split_owned:
                            self.native_event = True
                            try:
                                local_point = self.canvas.mapFromGlobal(event.globalPosition().toPoint())
                                QApplication.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, local_point, event.globalPosition(), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                            finally:
                                self.native_event = False
                            self.split_owned = True
                            self.split_key = key
                            self.canvas.grabMouse()
                        self.preview = SlidePreview(self, dock, edge)
                    self.preview.advance(min(0.99, (limit - wanted) / max(1, limit)))
                    if releasing and self.preview.fraction >= PARK_THRESHOLD:
                        self.collapse(dock, edge)
                    break
            if releasing and self.preview:
                self.return_preview()
            elif candidate is None:
                self.clear_preview()
                if self.split_owned and self.split_key in rects:
                    rect, edge, minimum = rects[self.split_key]
                    horizontal = edge in {"left", "right"}
                    wanted = rect.width() + (delta.x() if edge == "left" else -delta.x()) if horizontal else rect.height() + (delta.y() if edge == "top" else -delta.y())
                    self.resize_owned_split(self.docks[self.split_key], edge, wanted, minimum.width() if horizontal else minimum.height())
            if releasing:
                if delta.manhattanLength() > QApplication.startDragDistance():
                    footer = self.docks.get("actions")
                    if footer and not footer.isFloating() and "actions" in rects:
                        rect, edge, minimum = rects["actions"]
                        boundary = rect.top() if edge == "bottom" else rect.bottom()
                        if edge in {"top", "bottom"} and abs(self.canvas.mapFromGlobal(point).y() - boundary) < 14:
                            height = minimum.height() if candidate is footer else footer.height()
                            if self.owner.isMaximized():
                                self.maximized_footer_height = height
                            else:
                                self.footer_height = height
                self.press = None
                self.schedule_normalize()
                if self.split_owned:
                    self.split_owned = False
                    self.split_key = None
                    self.release_live_split()
                    if QWidget.mouseGrabber() is self.canvas:
                        self.canvas.releaseMouse()
                    return True
            elif candidate is not None or self.split_owned:
                # Native sizing got us to the minimum; our viewport now owns the
                # overshoot, otherwise Qt keeps reapplying the original width.
                return True
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(theme().colors["end"]))
        gradient.setColorAt(1, QColor(theme().colors["bg"]))
        painter.fillRect(self.rect(), gradient)
