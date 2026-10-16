"""Native QPushButton semantics with a restrained, animated tactile glass skin."""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QSize, QRectF, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QAccessible, QAccessibleEvent, QMouseEvent
from PySide6.QtWidgets import QPushButton

from installer.ui.tactile_material import manager_theme, paint_glass, rounded_path, tokens_for


class TactileGlassButton(QPushButton):
    def __init__(self, text="", parent=None, *, variant="pill", tone="neutral", size=44, material=None):
        if variant not in {"circle", "pill"} or tone not in {"neutral", "primary"} or size < 30:
            raise ValueError("Invalid tactile button variant, tone or size")
        super().__init__(text, parent)
        self.variant, self.tone, self.material = variant, tone, material
        self._minimum_height = size
        self._hover = self._press = 0.0
        self._keyboard_focus = self._enter_held = self._loading = False
        self._requested_enabled, self._progress = True, None
        self._reduce_motion = None
        self._angle = 0
        self._touch_id = None
        self._saved_description = ""
        self._icon_cache = None
        self.setAutoDefault(False)
        self.setAutoRepeat(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setMinimumSize(size if variant == "circle" else 44, size)
        self.hover_animation = self.animation("_hover")
        self.press_animation = self.animation("_press")
        self.pressed.connect(lambda: self.animate(self.press_animation, 1.0, self.tokens.press_ms))
        self.released.connect(lambda: self.animate(self.press_animation, 0.0, self.tokens.motion_ms))
        self.spinner = QTimer(self)
        self.spinner.setInterval(50)
        self.spinner.timeout.connect(self.advance_spinner)

    @property
    def tokens(self):
        return self.material or tokens_for(self.tone)

    @property
    def reduced_motion(self):
        current = manager_theme()
        return self._reduce_motion if self._reduce_motion is not None else bool(current and current.appearance["reduce_motion"])

    def setReducedMotion(self, value):
        self._reduce_motion = value
        self.refresh_appearance()

    def refresh_appearance(self):
        if self.reduced_motion:
            for animation, name in ((self.hover_animation, "_hover"), (self.press_animation, "_press")):
                if animation.state() == QVariantAnimation.State.Running:
                    final = animation.endValue()
                    animation.stop()
                    setattr(self, name, float(final))
        self.sync_spinner()
        self.update()

    def animation(self, field):
        animation = QVariantAnimation(self)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        def update(value):
            setattr(self, field, float(value))
            self.update()
        animation.valueChanged.connect(update)
        return animation

    def animate(self, animation, value, duration):
        field = "_hover" if animation is self.hover_animation else "_press"
        animation.stop()
        animation.setStartValue(getattr(self, field))
        animation.setEndValue(value)
        animation.setDuration(0 if self.reduced_motion else duration)
        animation.start()

    def sizeHint(self):
        height = max(self._minimum_height, self.fontMetrics().height() + 22)
        if self.variant == "circle":
            return QSize(height, height)
        icon = self.iconSize().width() + 8 if not self.icon().isNull() else 0
        return QSize(max(60, self.fontMetrics().horizontalAdvance(self.text()) + icon + 40), height)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return rounded_path(QRectF(self.rect()), min(self.width(), self.height()) / 2).contains(point)

    def enterEvent(self, event):
        if self.isEnabled():
            self.animate(self.hover_animation, 1.0, self.tokens.motion_ms)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate(self.hover_animation, 0.0, self.tokens.motion_ms)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def event(self, event):
        kind = event.type()
        if kind == QEvent.Type.WindowDeactivate and hasattr(self, "press_animation"):
            self._enter_held = False
            self._touch_id = None
            self.setDown(False)
            self.animate(self.press_animation, 0.0, self.tokens.motion_ms)
        if kind in {QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.TouchCancel} and hasattr(self, "_touch_id"):
            if kind == QEvent.Type.TouchCancel:
                self._touch_id = None
                self.setDown(False)
                self.animate(self.press_animation, 0.0, self.tokens.motion_ms)
                event.accept()
                return True
            if kind == QEvent.Type.TouchBegin:
                if not self.isEnabled() or not event.points():
                    return False
                self._touch_id = event.points()[0].id()
                self._keyboard_focus = False
            point = next((item for item in event.points() if item.id() == self._touch_id), None)
            if point is not None and self.isEnabled():
                mouse_type = QEvent.Type.MouseButtonPress if kind == QEvent.Type.TouchBegin else QEvent.Type.MouseButtonRelease if kind == QEvent.Type.TouchEnd else QEvent.Type.MouseMove
                mouse = QMouseEvent(mouse_type, point.position(), point.globalPosition(), Qt.MouseButton.LeftButton if mouse_type != QEvent.Type.MouseMove else Qt.MouseButton.NoButton, Qt.MouseButton.NoButton if kind == QEvent.Type.TouchEnd else Qt.MouseButton.LeftButton, event.modifiers())
                if kind == QEvent.Type.TouchBegin:
                    super().mousePressEvent(mouse)
                elif kind == QEvent.Type.TouchEnd:
                    super().mouseReleaseEvent(mouse)
                    self._touch_id = None
                else:
                    super().mouseMoveEvent(mouse)
            event.accept()
            return True
        return super().event(event)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in {Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason}
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self._keyboard_focus = self._enter_held = False
        self.setDown(False)
        self.animate(self.press_animation, 0.0, self.tokens.motion_ms)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and event.isAutoRepeat():
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter} and self.isEnabled():
            self._keyboard_focus = True
            self.update()
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if self.isEnabled() and not event.isAutoRepeat():
                self._enter_held = True
                self.setDown(True)
                self.pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and event.isAutoRepeat():
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if not event.isAutoRepeat():
                activate = self._enter_held and self.isEnabled()
                self._enter_held = False
                self.setDown(False)
                if activate:
                    self.click()
                self.animate(self.press_animation, 0.0, self.tokens.motion_ms)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def setEnabled(self, enabled):
        self._requested_enabled = bool(enabled)
        super().setEnabled(bool(enabled) and not getattr(self, "_loading", False))

    def setDisabled(self, disabled):
        self.setEnabled(not disabled)

    def setLoading(self, loading, *, progress=None):
        loading = bool(loading)
        if loading and not self._loading:
            self._saved_description = self.accessibleDescription()
        self._loading = loading
        self._progress = None if progress is None else max(0.0, min(1.0, float(progress)))
        super().setEnabled(self._requested_enabled and not loading)
        self.setAccessibleDescription(("正在处理" + (f"，{self._progress:.0%}" if self._progress is not None else "")) if loading else self._saved_description)
        QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.DescriptionChanged))
        self.sync_spinner()
        self.update()

    def sync_spinner(self):
        self.spinner.start() if self._loading and self.isVisible() and not self.reduced_motion and self._progress is None else self.spinner.stop()

    def advance_spinner(self):
        if not self.window().isMinimized():
            self._angle = (self._angle + 18) % 360
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_spinner()

    def hideEvent(self, event):
        self.spinner.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled() and hasattr(self, "press_animation"):
            self._enter_held = False
            self._touch_id = None
            self.setDown(False)
            for animation in (self.hover_animation, self.press_animation):
                animation.stop()
            self._hover = self._press = 0.0
            self.update()

    def visual_state(self):
        return self._hover, self._press, self._keyboard_focus and self.hasFocus()

    def tinted_icon(self, icon_size, color, ratio):
        key = (self.icon().cacheKey(), icon_size, ratio, color.name())
        if self._icon_cache is None or self._icon_cache[0] != key:
            pixmap = self.icon().pixmap(QSize(icon_size, icon_size), ratio)
            tinted = QPixmap(pixmap.size())
            # Set DPR before painting the mask, so it is rasterized exactly once.
            tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
            tinted.fill(Qt.GlobalColor.transparent)
            ink = QPainter(tinted)
            ink.drawPixmap(0, 0, pixmap)
            ink.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            ink.fillRect(tinted.rect(), color)
            ink.end()
            self._icon_cache = (key, tinted)
        return self._icon_cache[1]

    def paintEvent(self, event):
        current = manager_theme()
        if self.material is None and current and not current.appearance["glass_strength"]:
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        tokens = self.tokens
        hover, press, focus = self.visual_state()
        if not self.isEnabled():
            hover = press = 0.0
        physical_press = 0.0 if self.reduced_motion else press
        rect = QRectF(self.rect()).adjusted(6, 5, -6, -7)
        if self.variant == "circle":
            side = min(rect.width(), rect.height())
            rect = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
        paint_glass(painter, rect, min(rect.width(), rect.height()) / 2, tokens, hover=hover, press=press, enabled=self.isEnabled() or self._loading, focus=focus, motion_scale=0.0 if self.reduced_motion else 1.0)
        content = rect.translated(0, tokens.press_distance * physical_press)
        color = QColor(tokens.text if self.isEnabled() or self._loading else "#344852")
        painter.setFont(self.font())
        text = "" if self.variant == "circle" else self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, round(content.width() - 24 - (26 if self._loading or not self.icon().isNull() else 0))))
        icon_size = min(self.iconSize().width(), round(content.height() * .55)) if not self.icon().isNull() else 18 if self._loading else 0
        width = self.fontMetrics().horizontalAdvance(text) + (icon_size + 8 if icon_size and text else icon_size)
        x = content.center().x() - width / 2
        if icon_size:
            target = QRectF(x, content.center().y() - icon_size / 2, icon_size, icon_size)
            if self._loading:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawArc(target.adjusted(2, 2, -2, -2), -self._angle * 16, round((self._progress * 360 if self._progress is not None else 250) * 16))
            else:
                painter.drawPixmap(target.topLeft(), self.tinted_icon(icon_size, color, self.devicePixelRatioF()))
            x += icon_size + (8 if text else 0)
        if text:
            text_rect = QRectF(x, content.top(), self.fontMetrics().horizontalAdvance(text) + 1, content.height())
            painter.setPen(QColor(26, 43, 53, 90))
            painter.drawText(text_rect.translated(.2, .8), Qt.AlignmentFlag.AlignCenter, text)
            painter.setPen(color)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
