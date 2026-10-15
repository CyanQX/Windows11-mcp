"""An operation-only progress strip whose reserved height never changes."""
from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve, Qt
from PySide6.QtWidgets import QWidget, QProgressBar, QGraphicsOpacityEffect

from installer.ui.theme import column, label, row, theme


class TaskStrip(QWidget):
    SUCCESS_DELAY = 8000

    def __init__(self):
        super().__init__()
        self.setFixedHeight(58)
        self.content = QWidget(self)
        body = column(self, 0, 0)
        body.addWidget(self.content)
        stack = column(self.content, 0, 7)
        self.status = label("准备就绪", size=11, role="secondary")
        self.percent = label("0%", size=11, bold=True)
        stack.addWidget(row(self.status, None, self.percent))
        self.bar = QProgressBar()
        self.bar.setRange(0, 10000)
        self.bar.setTextVisible(False)
        self.bar.setAccessibleName("当前任务完成进度")
        stack.addWidget(self.bar)
        self.opacity = QGraphicsOpacityEffect(self.content)
        self.content.setGraphicsEffect(self.opacity)
        self.opacity.setOpacity(0)
        self.content.hide()
        self.delay = QTimer(self)
        self.delay.setTimerType(Qt.TimerType.PreciseTimer)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(self.fade)
        self.animation = QPropertyAnimation(self.opacity, b"opacity", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self.finish_fade)

    def begin(self, visible=True):
        self.delay.stop()
        self.animation.stop()
        self.opacity.setOpacity(1 if visible else 0)
        self.content.setVisible(visible)

    def complete(self, success):
        if success and not self.content.isHidden():
            self.delay.start(self.SUCCESS_DELAY)

    def fade(self):
        self.animation.stop()
        if theme().appearance["reduce_motion"]:
            self.opacity.setOpacity(0)
            self.finish_fade()
        else:
            self.animation.setStartValue(self.opacity.opacity())
            self.animation.setEndValue(0)
            self.animation.setDuration(320)
            self.animation.start()

    def finish_fade(self):
        if self.opacity.opacity() == 0:
            self.content.hide()  # The fixed-height parent stays in the log layout.
