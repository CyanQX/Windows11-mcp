"""Crisp selection controls with native checkbox semantics and quiet focus feedback."""
from PySide6.QtCore import Qt, QRectF, QSize, QPoint, QEvent, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QListView, QSizePolicy, QFrame, QVBoxLayout, QStyledItemDelegate, QStyle, QLabel

from installer.ui.theme import theme
from installer.ui.tactile_material import paint_glass, tokens_for


class ChoiceDelegate(QStyledItemDelegate):
    """Own every row state so the platform palette cannot turn hover text white."""
    def sizeHint(self, option, index):
        return QSize(120, 40)

    def paint(self, painter, option, index):
        c = theme().colors
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        if selected or hovered:
            color = QColor(c["accent"])
            color.setAlpha(45 if hovered else 26)
            painter.setBrush(color)
            painter.setPen(QPen(QColor(c["border"]), 1) if selected else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 9, 9)
        painter.setFont(option.font)
        painter.setPen(QColor(c["text"] if enabled else c["secondary"]))
        text = option.fontMetrics.elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, int(rect.width()) - 24)
        painter.drawText(rect.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class ChoicePopup(QFrame):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.setObjectName("choice-popup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.list = QListView(self)
        self.list.setObjectName("choice-list")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(2)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(ChoiceDelegate(self.list))
        self.list.setStyleSheet("QListView#choice-list { background: transparent; border: none; padding: 0; outline: none; }")
        self.list.clicked.connect(self.choose)
        self.list.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.addWidget(self.list)

    def choose(self, index):
        if index.isValid() and index.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.owner.setCurrentIndex(index.row())
            self.owner.activated.emit(index.row())
            self.owner.hidePopup()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
                self.choose(self.list.currentIndex())
                return True
            if event.key() in {Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab}:
                self.owner.hidePopup()
                return True
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        c = theme().colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(c["border"]), 1))
        painter.setBrush(QColor(c["glass"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 14, 14)


class ChoiceBox(QComboBox):
    """Rounded popup without native scroller strips; wheel never changes the choice."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = ChoicePopup(self)
        self._trigger_dismiss = False
        QApplication.instance().installEventFilter(self)
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaxVisibleItems(8)

    def showPopup(self):
        if self._trigger_dismiss:
            self._trigger_dismiss = False
            return
        if self.popup.isVisible():
            self.hidePopup()
            return
        if not self.isEnabled() or not self.count():
            return
        view = self.popup.list
        view.setModel(self.model())
        view.setModelColumn(self.modelColumn())
        view.setRootIndex(self.rootModelIndex())
        view.setCurrentIndex(self.model().index(self.currentIndex(), self.modelColumn(), self.rootModelIndex()))
        screen = self.screen().availableGeometry()
        width = min(screen.width() - 12, max(self.width(), self.sizeHint().width()))
        height = min(screen.height() - 12, 18 + min(self.count(), self.maxVisibleItems()) * 44)
        origin = self.mapToGlobal(QPoint(0, self.height() + 5))
        if origin.y() + height > screen.bottom():
            origin.setY(self.mapToGlobal(QPoint()).y() - height - 5)
        origin.setX(max(screen.left() + 6, min(origin.x(), screen.right() - width - 5)))
        origin.setY(max(screen.top() + 6, min(origin.y(), screen.bottom() - height - 5)))
        self.popup.setGeometry(origin.x(), origin.y(), width, height)
        self.popup.show()
        view.scrollTo(view.currentIndex())
        view.setFocus(Qt.FocusReason.PopupFocusReason)

    def hidePopup(self):
        self.popup.hide()
        super().hidePopup()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self.popup.isVisible() and self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint())):
                self._trigger_dismiss = True
                self.hidePopup()
                QTimer.singleShot(0, self.clear_trigger_dismiss)
        return super().eventFilter(watched, event)

    def clear_trigger_dismiss(self):
        self._trigger_dismiss = False

    def wheelEvent(self, event):
        event.ignore()


class StepValue(QLabel):
    """Display a number without allowing text selection or direct editing."""
    def __init__(self):
        super().__init__()
        self._value, self._minimum, self._maximum = 0, 0, 99
        self.setProperty("stepValue", True)
        self.setMinimumHeight(42)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def setRange(self, minimum, maximum):
        self._minimum, self._maximum = minimum, maximum
        self.setValue(self._value)

    def setValue(self, value):
        self._value = min(self._maximum, max(self._minimum, value))
        self.setText(str(self._value))

    def value(self):
        return self._value

    def stepDown(self):
        self.setValue(self._value - 1)

    def stepUp(self):
        self.setValue(self._value + 1)

    def wheelEvent(self, event):
        event.ignore()


class GlassCheckBox(QCheckBox):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(42)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 62, max(42, self.fontMetrics().height() + 20))

    def minimumSizeHint(self):
        return QSize(80, self.sizeHint().height())

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        current = theme()
        if current is None:
            return super().paintEvent(event)
        c = current.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        indicator = QRectF(10, (self.height() - 22) / 2, 22, 22)
        checked = self.checkState() != Qt.CheckState.Unchecked
        installed = bool(self.property("installed"))
        radius = 11 if installed else 7
        accent = QColor(c["accent"])
        if self.hasFocus():
            glow = QColor(accent)
            glow.setAlpha(70)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glow, 2))
            painter.drawRoundedRect(indicator.adjusted(-3, -3, 3, 3), 10, 10)
        fill = QLinearGradient(indicator.topLeft(), indicator.bottomRight())
        if (checked and self.isEnabled()) or installed:
            fill.setColorAt(0, QColor("#408EFF"))
            fill.setColorAt(1, QColor("#1266ED"))
            border = QColor("#1266ED")
        else:
            fill.setColorAt(0, QColor(c["glass"]))
            fill.setColorAt(1, QColor(c["bg"]))
            border = QColor(c["border"])
            if self.underMouse() and self.isEnabled():
                border = accent
        if current.appearance["glass_strength"] and (checked or installed):
            paint_glass(painter, indicator, radius, tokens_for("primary"), enabled=self.isEnabled(), shadow=False, texture=False)
        else:
            painter.setPen(QPen(border, 1.2))
            painter.setBrush(fill)
            painter.drawRoundedRect(indicator, radius, radius)
        if installed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(indicator.center(), 3.5, 3.5)
        elif checked:
            path = QPainterPath()
            if self.checkState() == Qt.CheckState.PartiallyChecked:
                path.moveTo(indicator.left() + 6, indicator.center().y())
                path.lineTo(indicator.right() - 6, indicator.center().y())
            else:
                path.moveTo(indicator.left() + 5.5, indicator.top() + 11)
                path.lineTo(indicator.left() + 9.5, indicator.top() + 15)
                path.lineTo(indicator.left() + 16.5, indicator.top() + 7.5)
            painter.setPen(QPen(QColor("#FFFFFF" if self.isEnabled() else c["secondary"]), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        text = QRectF(44, 0, max(0, self.width() - 54), self.height())
        painter.setFont(self.font())
        painter.setPen(QColor(c["text"] if self.isEnabled() else c["secondary"]))
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text.width())))
