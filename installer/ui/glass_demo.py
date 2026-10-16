"""Interactive native material bench; files are selected and hashed locally only."""
from __future__ import annotations

import hashlib
import json
import queue
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QBoxLayout, QCheckBox, QDialog, QFileDialog, QGridLayout, QLineEdit, QProgressBar, QScrollArea, QWidget

from installer.ui.controls import ChoiceBox
from installer.ui.tactile_button import TactileGlassButton
from installer.ui.tactile_material import TactileTokens
from installer.ui.theme import column, label, row

HERO_MATERIAL = replace(TactileTokens(), edge="#8D9BA3", shoulder="#73848D", core="#6B7E88", text="#F4F9FC", ambient_alpha=28, contact_alpha=32)


class StateSample(TactileGlassButton):
    """Material swatches only; real activation is exercised on the hero controls."""
    def __init__(self, state, parent=None):
        super().__init__("Upload", parent, size=56, material=TactileTokens())
        self.sample_state = state
        if state == "Disabled":
            self.setEnabled(False)
        elif state == "Loading":
            self.setLoading(True)

    def visual_state(self):
        hover, press, focus = super().visual_state()
        return max(hover, self.sample_state == "Hover"), max(press, self.sample_state == "Pressed"), focus or self.sample_state == "Focus-visible"


def hash_local_file(path, cancel, report):
    original = path.stat()
    digest, completed, reported = hashlib.sha256(), 0, -1
    with path.open("rb") as stream:
        while True:
            if cancel.is_set():
                return None
            block = stream.read(256 * 1024)
            if not block:
                break
            digest.update(block)
            completed += len(block)
            if completed > original.st_size:
                raise ValueError("读取期间文件发生变化，请重新选择")
            fraction = completed / original.st_size if original.st_size else 0
            if int(fraction * 100) != reported:
                report(fraction)
                reported = int(fraction * 100)
    current = path.stat()
    if (current.st_size, current.st_mtime_ns) != (original.st_size, original.st_mtime_ns):
        raise ValueError("读取期间文件发生变化，请重新选择")
    return {"name": path.name, "bytes": completed, "sha256": digest.hexdigest()}


class GlassDemo(QDialog):
    def __init__(self, base, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Windows MCP · 3D 触感玻璃交互预览")
        bounds = self.screen().availableGeometry()
        self.resize(min(1040, bounds.width() - 40), min(820, bounds.height() - 60))
        self.setMinimumSize(360, min(520, bounds.height() - 60))
        self.base, self.dark = base, False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy, self.result, self.activations = False, None, 0
        root = column(self, 20, 16)
        self.title = label("3D 触感玻璃", size=21, bold=True)
        self.background = ChoiceBox()
        self.background.addItem("冷灰蓝背景", False)
        self.background.addItem("深色背景", True)
        self.background.setMaximumWidth(160)
        self.background.currentIndexChanged.connect(self.change_background)
        root.addWidget(row(self.title, None, self.background))
        scroll, content = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        body = column(content, 12, 18)
        self.hero = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.hero.setSpacing(26)
        self.hero.setContentsMargins(12, 18, 12, 18)
        self.circle = TactileGlassButton(variant="circle", size=144, material=HERO_MATERIAL)
        arrow = base / "installer/resources/icons/arrow-up.svg"
        if not QSvgRenderer(str(arrow)).isValid():
            raise ValueError("Arrow icon is invalid")
        self.circle.setIcon(QIcon(str(arrow)))
        self.circle.setIconSize(self.circle.sizeHint() * .38)
        self.circle.setAccessibleName("选择本地文件")
        self.upload = TactileGlassButton("Upload", size=144, material=HERO_MATERIAL)
        self.upload.setFont(QFont("Segoe UI", 36, QFont.Weight.Light))
        self.upload.setStyleSheet("font-family:'Segoe UI'; font-weight:300;")
        self.circle.clicked.connect(self.choose_file)
        self.upload.clicked.connect(self.choose_file)
        self.hero.addStretch()
        self.hero.addWidget(self.circle, 0, Qt.AlignmentFlag.AlignCenter)
        self.hero.addWidget(self.upload, 0, Qt.AlignmentFlag.AlignCenter)
        self.hero.addStretch()
        body.addLayout(self.hero)
        self.states_title = label("状态对照", size=14, bold=True)
        body.addWidget(self.states_title)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self.samples, self.cells, self.captions = [], [], []
        for state in ("Default", "Hover", "Pressed", "Focus-visible", "Disabled", "Loading"):
            cell = QWidget()
            group = column(cell, 2, 4)
            control = StateSample(state)
            control.clicked.connect(lambda checked=False, name=state: self.activated(name))
            caption = label(state, size=10)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            group.addWidget(control)
            group.addWidget(caption)
            self.samples.append(control)
            self.cells.append(cell)
            self.captions.append(caption)
        body.addLayout(self.grid)
        self.file_name = QLineEdit()
        self.file_name.setReadOnly(True)
        self.file_name.setPlaceholderText("尚未选择文件")
        self.file_name.setAccessibleName("选中的本地文件")
        body.addWidget(self.file_name)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("本地文件读取进度")
        self.progress.hide()
        body.addWidget(self.progress)
        self.feedback = label("文件仅在本地读取。", size=11, wrap=True)
        body.addWidget(self.feedback)
        self.details = QLineEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("文件 SHA-256")
        self.details.hide()
        body.addWidget(self.details)
        body.addStretch()
        self.reduced = QCheckBox("减少动画")
        self.reduced.toggled.connect(self.reduce_motion)
        self.cancel_button = TactileGlassButton("取消读取", material=TactileTokens())
        self.cancel_button.clicked.connect(self.cancel_read)
        self.cancel_button.hide()
        root.addWidget(row(self.reduced, None, self.cancel_button))
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)
        self._columns = None
        self.change_background()
        self.arrange()

    def paintEvent(self, event):
        painter = QPainter(self)
        fill = QLinearGradient(0, 0, self.width(), self.height())
        fill.setColorAt(0, QColor("#455D6B" if self.dark else "#8D9FA8"))
        fill.setColorAt(.6, QColor("#304855" if self.dark else "#7D9099"))
        fill.setColorAt(1, QColor("#253C49" if self.dark else "#83969F"))
        painter.fillRect(self.rect(), fill)
        painter.setPen(QPen(QColor(224, 242, 249, 55), 1))
        painter.drawRoundedRect(self.rect().adjusted(10, 70, -10, -60), 30, 30)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "grid"):
            self.arrange()

    def arrange(self):
        narrow = self.width() < 700
        self.hero.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.circle.setFixedSize(120 if narrow else 164, 120 if narrow else 164)
        self.circle.setIconSize(self.circle.size() * .36)
        self.upload.setFixedSize(min(320, self.width() - 100) if narrow else 380, 106 if narrow else 164)
        self.upload.setFont(QFont("Segoe UI", 30 if narrow else 38, QFont.Weight.Light))
        columns = 2 if narrow else 3
        if columns != self._columns:
            for cell in self.cells:
                self.grid.removeWidget(cell)
            for index, cell in enumerate(self.cells):
                self.grid.addWidget(cell, index // columns, index % columns)
            self._columns = columns

    def change_background(self, *_):
        self.dark = bool(self.background.currentData())
        color = "#F1F6F8" if self.dark else "#0B202B"
        for item in (self.title, self.states_title, self.feedback, *self.captions):
            item.setStyleSheet("color:" + color)
        self.reduced.setStyleSheet("QCheckBox { color:" + color + "; spacing:8px; }")
        for control in (self.circle, self.upload, self.cancel_button, *self.samples):
            control.material = replace(HERO_MATERIAL if control in (self.circle, self.upload) else TactileTokens(), focus="#C3E9FA" if self.dark else "#164668")
            control.update()
        self.update()

    def reduce_motion(self, value):
        for control in self.findChildren(TactileGlassButton):
            control.setReducedMotion(value)

    def activated(self, name):
        self.activations += 1
        self.feedback.setText(f"已激活 {name} · {self.activations} 次")

    def choose_file(self):
        if self.busy:
            return
        options = QFileDialog.Option.DontUseNativeDialog if QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs) else QFileDialog.Option(0)
        path, _ = QFileDialog.getOpenFileName(self, "选择本地文件", options=options)
        if path:
            self.read_file(Path(path))

    def read_file(self, path):
        if self.busy:
            return False
        self.busy, self.result = True, None
        self.cancel = threading.Event()
        self.file_name.setText(path.name)
        self.file_name.setToolTip(str(path))
        self.feedback.setText("正在读取本地文件…")
        self.details.hide()
        for control in (self.circle, self.upload):
            control.setLoading(True)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        events, cancel = self.events, self.cancel
        def worker():
            try:
                result = hash_local_file(path, cancel, lambda fraction: events.put(("progress", fraction)))
                events.put(("done", result))
            except Exception as exc:
                events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="glass-local-file-read").start()
        self.timer.start()
        return True

    def cancel_read(self):
        self.cancel.set()
        self.feedback.setText("正在取消本地读取…")

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.progress.setValue(min(999, round(value * 1000)))
                for control in (self.circle, self.upload):
                    control.setLoading(True, progress=value)
            else:
                self.timer.stop()
                self.busy = False
                for control in (self.circle, self.upload):
                    control.setLoading(False)
                self.cancel_button.hide()
                if kind == "error":
                    self.feedback.setText("读取未完成：" + value)
                elif value is None or self.cancel.is_set():
                    self.feedback.setText("已取消本地读取")
                else:
                    self.result = value
                    self.progress.setValue(1000)
                    self.details.setText(value["sha256"])
                    self.details.show()
                    self.feedback.setText(f"已选择 {value['name']} · {value['bytes']:,} 字节 · 本地校验完成")

    def closeEvent(self, event):
        self.cancel.set()
        self.timer.stop()
        super().closeEvent(event)


def open_demo(owner):
    demo = GlassDemo(owner.base, owner)
    owner.dialogs.append(demo)
    demo.finished.connect(lambda _: owner.dialogs.remove(demo) if demo in owner.dialogs else None)
    demo.show()
    return demo


def run(base, *, capture_path=None, size=None, scale=1.0, verify=False, dark=False):
    import os
    if not .75 <= scale <= 2:
        raise ValueError("Preview scale outside bounds")
    if scale != 1:
        os.environ["QT_SCALE_FACTOR"] = str(scale)
    from installer.settings import SettingsService
    from installer.ui.theme import Theme, application
    if verify:
        from PySide6.QtWidgets import QApplication
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app = application()
    with tempfile.TemporaryDirectory(prefix="windows-mcp-glass-demo-") as temporary:
        home = Path(temporary)
        settings = SettingsService(home, env={"LOCALAPPDATA": str(home / "local")})
        Theme(settings, base).apply()
        demo = GlassDemo(base)
        if dark:
            demo.background.setCurrentIndex(1)
        if size:
            demo.resize(*size)
        demo.show()
        errors = []
        if capture_path:
            def take():
                try:
                    from installer.ui.capture import capture
                    if verify:
                        from installer.ui.tactile_acceptance import verify_demo
                        checks = verify_demo(demo, capture_path, home)
                    else:
                        checks = []
                    capture(demo, capture_path)
                    origin = demo.circle.mapTo(demo, QPoint())
                    capture_path.with_suffix(".json").write_text(json.dumps({"native": True, "width": demo.width(), "height": demo.height(), "scale": scale, "pixel_ratio": demo.devicePixelRatioF(), "circle_bounds": [origin.x(), origin.y(), demo.circle.width(), demo.circle.height()], "circle_icon_size": demo.circle.iconSize().width(), "states": [item.sample_state for item in demo.samples], "interaction_checks": checks}, indent=2), encoding="utf-8")
                except Exception as exc:
                    import traceback
                    errors.append(f"{type(exc).__name__}: {exc}\n" + traceback.format_exc())
                finally:
                    demo.close()
            QTimer.singleShot(600, take)
        app.exec()
        if errors:
            raise RuntimeError("; ".join(errors))
