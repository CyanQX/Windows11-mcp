"""Opt-in acceptance against the actual frozen native controls and file picker."""
import hashlib
import time

from PySide6.QtCore import Qt, QEvent, QPointF, QTimer, QEventLoop
from PySide6.QtGui import QAccessible, QEnterEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit

from installer.ui.capture import capture


def wait(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def mouse(control, kind, *, outside=False):
    point = QPointF(-10, -10) if outside else QPointF(control.rect().center())
    event = QMouseEvent(kind, point, QPointF(control.mapToGlobal(point.toPoint())), Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(control, event)


def verify_demo(demo, target, temporary):
    checks, clicks = [], []
    control = demo.upload
    control.clicked.disconnect(demo.choose_file)
    control.clicked.connect(lambda checked=False: clicks.append(checked))
    original = control.size()
    center = QPointF(control.rect().center())
    QApplication.sendEvent(control, QEnterEvent(center, center, QPointF(control.mapToGlobal(center.toPoint()))))
    wait(180)
    assert control._hover > .95
    checks.append("hover")
    mouse(control, QEvent.Type.MouseButtonPress)
    wait(120)
    assert control.isDown() and control._press > .95 and not clicks
    capture(demo, target.with_name(target.stem + "-pressed.png"))
    mouse(control, QEvent.Type.MouseButtonRelease)
    wait(180)
    assert len(clicks) == 1 and not control.isDown() and control._press == 0
    assert control.size() == original and not control.isCheckable()
    checks += ["pressed", "release", "stable_geometry"]
    mouse(control, QEvent.Type.MouseButtonPress)
    mouse(control, QEvent.Type.MouseMove, outside=True)
    mouse(control, QEvent.Type.MouseButtonRelease, outside=True)
    wait(160)
    assert len(clicks) == 1
    checks.append("release_outside_cancelled")
    control.clearFocus()
    control.setFocus(Qt.FocusReason.TabFocusReason)
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
        before = len(clicks)
        QApplication.sendEvent(control, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        assert control.isDown() and len(clicks) == before
        QApplication.sendEvent(control, QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier))
        assert len(clicks) == before + 1 and not control.isDown()
    assert control._keyboard_focus
    capture(demo, target.with_name(target.stem + "-focus.png"))
    checks += ["space", "enter", "return", "focus_visible"]
    before = len(clicks)
    control.setEnabled(False)
    control.click()
    mouse(control, QEvent.Type.MouseButtonPress)
    mouse(control, QEvent.Type.MouseButtonRelease)
    assert len(clicks) == before
    control.setEnabled(True)
    control.setLoading(True, progress=.3)
    control.click()
    assert len(clicks) == before and control.size() == original
    interface = QAccessible.queryAccessibleInterface(control)
    assert interface.role() == QAccessible.Role.Button and interface.state().disabled
    assert "30%" in control.accessibleDescription()
    control.setLoading(False)
    checks += ["disabled", "loading_blocks_repeat", "accessible_button"]
    control.setReducedMotion(True)
    mouse(control, QEvent.Type.MouseButtonPress)
    assert control._press == 1 and control.press_animation.state() != control.press_animation.State.Running
    mouse(control, QEvent.Type.MouseButtonRelease)
    control.setReducedMotion(False)
    checks.append("reduced_motion")

    # Use Qt's actual file chooser, in an isolated temporary directory. No network.
    payload = temporary / "glass-preview-example.txt"
    data = b"Windows MCP tactile glass: local file selection\n" * 100
    payload.write_bytes(data)
    picker_seen = []
    def select():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QFileDialog):
            dialog.setDirectory(str(temporary))
            dialog.selectFile(str(payload))
            filename = dialog.findChild(QLineEdit, "fileNameEdit")
            assert filename is not None, "Qt file picker has no file-name editor"
            filename.setText(str(payload))
            picker_seen.append(dialog.selectedFiles())
            QTimer.singleShot(180, dialog.accept)
        else:
            QTimer.singleShot(30, select)
    def abort():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QFileDialog):
            dialog.reject()
    guard = QTimer(demo)
    guard.setSingleShot(True)
    guard.timeout.connect(abort)
    guard.start(5000)
    QTimer.singleShot(50, select)
    control.clicked.connect(demo.choose_file)
    control.click()
    guard.stop()
    deadline = time.monotonic() + 5
    while demo.busy and time.monotonic() < deadline:
        wait(30)
    assert picker_seen and demo.result is not None, {"picker": picker_seen, "feedback": demo.feedback.text(), "file": demo.file_name.text()}
    assert demo.result["name"] == payload.name and demo.result["sha256"] == hashlib.sha256(data).hexdigest()
    assert demo.progress.value() == 1000 and demo.circle.isEnabled() and control.isEnabled()
    checks += ["real_qt_file_picker", "local_file_name", "real_local_sha256"]
    return checks
