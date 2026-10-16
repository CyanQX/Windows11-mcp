"""Native glass controls: activation, cancellation, touch, accessibility and local IO."""
import hashlib
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QAccessible, QIcon, QColor
from PySide6.QtTest import QTest

from installer.ui.glass_demo import GlassDemo, HERO_MATERIAL, hash_local_file
from installer.ui.tactile_button import TactileGlassButton
from installer.ui.tactile_material import TactileTokens, tokens_for
from installer.ui.theme import application

ROOT = Path(__file__).resolve().parents[1]


def wait_for_release(button):
    # Qt's animation timer can deliver its final frame late on a busy CI
    # desktop. Wait for the real endpoint, rather than a fixed frame budget.
    deadline = time.monotonic() + 1
    while button._press != 0 and time.monotonic() < deadline:
        QTest.qWait(20)


@pytest.fixture
def glass():
    app = application()
    button = TactileGlassButton("Upload", material=TactileTokens())
    button.resize(260, 70)
    button.show()
    button.activateWindow()
    QTest.qWait(50)
    yield button
    button.close()
    app.processEvents()


def test_mouse_press_releases_once_without_becoming_selected(glass):
    clicks = []
    glass.clicked.connect(lambda checked=False: clicks.append(checked))
    original = glass.geometry()
    QTest.mousePress(glass, Qt.MouseButton.LeftButton, pos=glass.rect().center())
    QTest.qWait(110)
    assert glass.isDown() and glass._press > .95
    assert glass.geometry() == original and not glass.isCheckable()
    QTest.mouseRelease(glass, Qt.MouseButton.LeftButton, pos=glass.rect().center())
    wait_for_release(glass)
    assert len(clicks) == 1 and not glass.isDown() and glass._press == 0


def test_drag_release_outside_never_activates(glass):
    clicks = []
    glass.clicked.connect(lambda checked=False: clicks.append(checked))
    QTest.mousePress(glass, Qt.MouseButton.LeftButton, pos=glass.rect().center())
    QTest.mouseMove(glass, QPoint(-20, -20))
    QTest.mouseRelease(glass, Qt.MouseButton.LeftButton, pos=QPoint(-20, -20))
    wait_for_release(glass)
    assert not clicks and not glass.isDown() and glass._press == 0


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_keyboard_activates_only_on_release_and_has_focus(glass, key):
    clicks = []
    glass.clicked.connect(lambda checked=False: clicks.append(checked))
    glass.clearFocus()
    glass.setFocus(Qt.FocusReason.TabFocusReason)
    assert glass._keyboard_focus and glass.hasFocus()
    QTest.keyPress(glass, key)
    QTest.qWait(110)
    assert glass.isDown() and not clicks
    QTest.keyRelease(glass, key)
    QTest.qWait(160)
    assert len(clicks) == 1 and not glass.isDown() and not glass.isChecked()


def test_disabled_and_busy_controls_block_all_activation_and_keep_dimensions(glass):
    clicks = []
    glass.clicked.connect(lambda checked=False: clicks.append(checked))
    original = glass.size()
    glass.setEnabled(False)
    glass.click()
    QTest.keyClick(glass, Qt.Key.Key_Return)
    QTest.mouseClick(glass, Qt.MouseButton.LeftButton)
    assert not clicks
    glass.setEnabled(True)
    glass.setLoading(True, progress=.3)
    assert not glass.isEnabled() and glass.size() == original
    assert "30%" in glass.accessibleDescription()
    glass.click()
    assert not clicks
    glass.setEnabled(False)  # A caller's disable must survive the end of loading.
    glass.setLoading(False)
    assert not glass.isEnabled()


def test_loading_animation_stops_offscreen_and_reduced_motion_has_no_tween(glass):
    glass.setLoading(True)
    assert glass.spinner.isActive()
    glass.hide()
    assert not glass.spinner.isActive()
    glass.show()
    glass.setReducedMotion(True)
    assert not glass.spinner.isActive()
    glass.setLoading(False)
    QTest.mousePress(glass, Qt.MouseButton.LeftButton, pos=glass.rect().center())
    assert glass._press == 1 and glass.press_animation.state() != glass.press_animation.State.Running
    QTest.mouseRelease(glass, Qt.MouseButton.LeftButton, pos=glass.rect().center())
    assert glass._press == 0


def test_native_set_disabled_survives_loading(glass):
    glass.setDisabled(True)
    glass.setLoading(True)
    glass.setLoading(False)
    assert not glass.isEnabled()
    glass.setDisabled(False)
    assert glass.isEnabled()


def test_vector_icon_retains_its_logical_size_and_centre_at_high_dpi(glass):
    glass.setIcon(QIcon(str(ROOT / "installer/resources/icons/arrow-up.svg")))
    logical = []
    for ratio in (1.0, 1.5, 2.0):
        image = glass.tinted_icon(60, QColor("#F4F9FC"), ratio).toImage()
        points = [(x, y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha() > 200]
        left, top = min(x for x, _ in points), min(y for _, y in points)
        right, bottom = max(x for x, _ in points), max(y for _, y in points)
        dpr = image.devicePixelRatio()
        logical.append(((right - left + 1) / dpr, (bottom - top + 1) / dpr))
        assert abs((left + right) / (2 * dpr) - 30) < 2
        assert abs((top + bottom) / (2 * dpr) - 30) < 2
    assert max(width for width, _ in logical) - min(width for width, _ in logical) < 1.5
    assert max(height for _, height in logical) - min(height for _, height in logical) < 1.5


def test_accessible_native_role_and_focus_are_preserved(glass):
    glass.setAccessibleName("选择本地文件")
    interface = QAccessible.queryAccessibleInterface(glass)
    assert interface.role() == QAccessible.Role.Button
    assert interface.text(QAccessible.Text.Name) == "选择本地文件"
    glass.setFocus(Qt.FocusReason.TabFocusReason)
    assert interface.state().focusable and interface.state().focused
    glass.setEnabled(False)
    assert interface.state().disabled


def test_touch_press_and_release_follow_native_button_semantics(glass):
    clicks = []
    glass.clicked.connect(lambda checked=False: clicks.append(checked))
    device = QTest.createTouchDevice()
    position = glass.rect().center()
    QTest.touchEvent(glass, device).press(0, position, glass).commit()
    QTest.qWait(120)
    assert glass.isDown() and glass._press > .95
    QTest.touchEvent(glass, device).release(0, position, glass).commit()
    wait_for_release(glass)
    assert len(clicks) == 1 and not glass.isDown() and glass._press == 0


def test_circle_target_is_round_and_touch_target_has_minimum_size():
    application()
    button = TactileGlassButton(variant="circle", size=64, material=TactileTokens())
    button.resize(64, 64)
    assert button.sizeHint().width() >= 44 and button.sizeHint().height() >= 44
    assert button.hitButton(button.rect().center())
    assert not button.hitButton(QPoint(0, 0))
    button.close()


def contrast(a, b):
    def luminance(color):
        parts = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in parts]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    lower, upper = sorted((luminance(a), luminance(b)))
    return (upper + .05) / (lower + .05)


def test_readable_normal_disabled_and_large_reference_text():
    application()
    for tone in ("neutral", "primary"):
        material = tokens_for(tone)
        assert contrast(material.text, material.core) >= 4.5
        assert contrast(material.text, material.shoulder) >= 4.5
    assert contrast("#344852", "#B7C3CA") >= 4.5
    assert contrast(HERO_MATERIAL.text, HERO_MATERIAL.core) >= 3  # Large Upload text.


def test_demo_file_picker_reads_real_file_without_upload(tmp_path, monkeypatch):
    application()
    demo = GlassDemo(ROOT)
    demo.show()
    payload = tmp_path / "example.txt"
    payload.write_bytes(b"local-file-check\n" * 1000)
    picker_calls = []
    def choose(*args, **kwargs):
        picker_calls.append(True)
        return str(payload), ""
    monkeypatch.setattr("installer.ui.glass_demo.QFileDialog.getOpenFileName", choose)
    demo.upload.click()
    assert len(picker_calls) == 1 and demo.busy
    assert not demo.read_file(payload)
    deadline = time.monotonic() + 5
    while demo.busy and time.monotonic() < deadline:
        QTest.qWait(20)
        time.sleep(.005)
    assert demo.result["name"] == payload.name
    assert demo.result["sha256"] == hashlib.sha256(payload.read_bytes()).hexdigest()
    assert demo.file_name.text() == payload.name and demo.progress.value() == 1000
    assert "本地校验完成" in demo.feedback.text() and demo.upload.isEnabled()
    demo.resize(420, 780)
    QTest.qWait(40)
    assert demo._columns == 2 and demo.upload.width() <= 320
    demo.close()


def test_cancelled_file_read_has_no_success_or_digest(tmp_path):
    payload = tmp_path / "example"
    payload.write_bytes(b"data")
    cancel = threading.Event()
    cancel.set()
    assert hash_local_file(payload, cancel, lambda _: pytest.fail("reported after cancel")) is None
