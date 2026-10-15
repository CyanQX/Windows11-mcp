"""Exercise the actual Qt widgets, dock persistence and background work lifecycle."""
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QProgressBar

from installer.settings import SettingsService
from installer.models import SelectionModel
from installer.ui.dialogs import Targets, StatusDialog
from installer.ui.main_window import MainWindow
from installer.ui.settings_dialog import Appearance, SettingsHub, Preferences
from installer.ui.theme import application
from tests.installer_helpers import make_service

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_window(tmp_path_factory):
    app = application()
    tmp = tmp_path_factory.mktemp("native-manager")
    service = make_service(tmp)
    settings = SettingsService(tmp / "ui-home", env={"LOCALAPPDATA": str(tmp / "ui-home/appdata")})
    window = MainWindow(ROOT, settings, service.backend.source_executable, service=service, auto_scan=False)
    window.show()
    QTest.qWait(100)
    yield window
    window.close()
    app.processEvents()


@pytest.fixture
def window(native_window, tmp_path):
    for dialog in list(native_window.dialogs):
        dialog.close()
    if native_window.selector.popup:
        native_window.selector.popup.close()
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    native_window.service = service
    service.log.callback = lambda event: native_window.events.put(("log", event))
    native_window.selection = SelectionModel()
    native_window.selector.model = native_window.selection
    native_window.accept_states(service.inspect_all())
    native_window.workspace.reset()
    QTest.qWait(50)
    return native_window


def test_installed_checkbox_and_select_all_cannot_override_eligibility(window):
    window.selector.toggle()
    checkbox = window.selector.checks["codex"]
    assert not checkbox.isEnabled()
    checkbox.click()
    assert not checkbox.isChecked()
    window.selector.choose("codex", True)
    window.selector.select_all()
    assert "codex" not in window.selection.selected
    assert window.selector.checks["claude"].isChecked()


def test_uninstall_chooses_installed_target_independently(window):
    assert window.uninstall_button.isEnabled()
    dialog = Targets(window, "uninstall")
    assert "codex" in dialog.variables
    assert not any(item.isChecked() for item in dialog.variables.values())
    assert not dialog.next_button.isEnabled()
    dialog.variables["codex"].click()
    assert dialog.next_button.isEnabled()
    dialog.close()


def test_refresh_preserves_popup_and_clears_only_ineligible_selections(window):
    window.selector.toggle()
    popup = window.selector.popup
    window.selector.choose("claude", True)
    assert window.selector.popup is popup and popup.isVisible()
    window.service.install_targets(["claude"])
    window.accept_states(window.service.inspect_all())
    assert not window.selection.selected
    assert not window.install_button.isEnabled()
    assert window.selector.popup is popup
    assert not window.selector.checks["claude"].isEnabled()


def test_status_shows_readable_facts_and_collapses_raw_details(window):
    dialog = StatusDialog(window)
    assert any("MCP 配置" in item.text() for item in dialog.findChildren(QLabel))
    details = dialog.findChildren(QPlainTextEdit)
    assert details and all(item.isHidden() for item in details)
    dialog.close()


def test_docks_swap_float_collapse_restore_and_persist(window):
    space = window.workspace
    left, right = space.docks["targets"], space.docks["maintenance"]
    space.swap(left, right)
    assert space.canvas.dockWidgetArea(left) == Qt.DockWidgetArea.RightDockWidgetArea
    right.setFloating(True)
    right.move(window.pos())
    assert right.isFloating()
    space.collapse(left)
    assert left.isVisible()  # Slide first; park only after the animation.
    QTest.qWait(310)
    assert left.isHidden() and space.collapsed["targets"][0] == "right"
    snapshot = space.snapshot()
    space.reset()
    space.load(snapshot)
    assert space.docks["maintenance"].isFloating()
    assert "targets" in space.collapsed
    space.collapsed["targets"][1].control.click()
    assert left.isVisible() and "targets" not in space.collapsed
    space.load({"schema": 1, "state": "broken!"})
    assert not space.collapsed
    assert all(not item.isFloating() for item in space.docks.values())


def test_separator_drag_past_minimum_parks_panel_and_handle_drag_restores(window):
    canvas = window.workspace.canvas
    dock = window.workspace.docks["targets"]
    point = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
    global_point = canvas.mapToGlobal(point)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, point, global_point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert window.workspace.eventFilter(canvas, press) is False
    minimum = dock.minimumSizeHint().width()
    canvas.resizeDocks([dock, window.workspace.docks["maintenance"]], [minimum, canvas.width() - minimum - 12], Qt.Orientation.Horizontal)
    QTest.qWait(30)
    destination = QPoint(dock.minimumSizeHint().width() - 100, point.y())
    move = QMouseEvent(QEvent.Type.MouseMove, destination, canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.workspace.eventFilter(canvas, move)
    assert dock.isVisible() and "targets" not in window.workspace.collapsed
    assert window.workspace.preview.fraction >= 0.75
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, destination, canvas.mapToGlobal(destination), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    window.workspace.eventFilter(canvas, release)
    QTest.qWait(310)
    assert "targets" in window.workspace.collapsed
    handle = window.workspace.collapsed["targets"][1]
    origin = handle.control.mapToGlobal(QPoint(5, 5))
    handle.eventFilter(handle.control, QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(5, 5), origin, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    handle.eventFilter(handle.control, QMouseEvent(QEvent.Type.MouseMove, QPoint(45, 5), origin + QPoint(40, 0), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert "targets" not in window.workspace.collapsed and dock.isVisible()


def test_footer_remains_compact_when_both_content_panels_are_parked(window):
    space = window.workspace
    space.collapse(space.docks["targets"])
    space.collapse(space.docks["maintenance"])
    QTest.qWait(350)
    assert space.placeholder.isVisible()
    assert space.docks["actions"].height() < 230
    assert space.collapsed["targets"][1].control.width() <= 38
    space.reset()


def test_appearance_updates_without_rescanning_or_recreating_service(window):
    service, events = window.service, list(window.service.log.events)
    popup = Appearance(window)
    popup.controls["theme"].setCurrentIndex(popup.controls["theme"].findData("warm"))
    assert window.theme.mode == "warm"
    assert window.service is service and window.service.log.events == events
    popup.controls["log_background"].setCurrentIndex(popup.controls["log_background"].findData("dark"))
    assert "#18283D" in window.logs.styleSheet()
    popup.controls["theme"].setCurrentIndex(popup.controls["theme"].findData("light"))
    popup.close()


def test_settings_order_and_no_external_adoption_menu(window):
    popup = SettingsHub(window)
    assert popup.names == ["外观", "高级设置", "配置与恢复", "安装目录", "检查更新", "日志与诊断", "关于"]
    popup.close()


def test_advanced_settings_groups_values_and_limits_codex_hint_to_codex(window):
    dialog = Preferences(window)
    QTest.qWait(50)
    assert dialog.body.contentsMargins().left() >= 24
    labels = [item.text() for item in dialog.findChildren(QLabel)]
    assert "连接与日志" in labels and "编程端程序路径" in labels
    assert sum("Codex CLI" in text for text in labels) == 1
    assert dialog.fields["health_timeout"].width() >= 100
    editor = dialog.fields["health_timeout"].lineEdit()
    assert editor.width() >= editor.fontMetrics().horizontalAdvance(editor.displayText()) + 24
    assert dialog.fields["exe:deepseek"].accessibleName() == "DeepSeek Harness"
    dialog.close()


def test_no_duplicate_brand_banner_and_footer_contains_only_action_controls(window):
    assert not any(item.text() == "Windows MCP" for item in window.findChildren(QLabel))
    assert not window.workspace.docks["actions"].findChildren(QProgressBar)
    assert window.settings_button.isVisible() and window.approval_button.isVisible()


def test_progress_fades_without_moving_logs_and_new_task_cancels_old_timeout(window):
    strip = window.task_strip
    strip.begin()
    QTest.qWait(30)
    before = window.logs.geometry()
    strip.complete(True)
    assert strip.delay.interval() == 8000
    strip.delay.start(40)
    QTest.qWait(420)
    assert strip.content.isHidden() and strip.height() == 58
    assert window.logs.geometry() == before
    strip.begin()
    strip.complete(True)
    strip.begin()
    assert not strip.delay.isActive() and strip.content.isVisible()
    strip.complete(False)
    assert not strip.delay.isActive() and strip.content.isVisible()


def test_success_hide_timer_also_runs_while_maintenance_panel_is_parked(window):
    strip = window.task_strip
    strip.begin()
    dock = window.workspace.docks["maintenance"]
    dock.hide()
    assert not strip.content.isVisible() and not strip.content.isHidden()
    strip.complete(True)
    assert strip.delay.isActive()
    strip.delay.start(35)
    QTest.qWait(420)
    dock.show()
    assert strip.content.isHidden()


def test_drag_short_of_edge_threshold_returns_without_parking(window):
    space, canvas = window.workspace, window.workspace.canvas
    dock = space.docks["targets"]
    point = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
    space.eventFilter(canvas, QMouseEvent(QEvent.Type.MouseButtonPress, point, canvas.mapToGlobal(point), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    minimum = dock.minimumSizeHint().width()
    canvas.resizeDocks([dock, space.docks["maintenance"]], [minimum, canvas.width() - minimum - 12], Qt.Orientation.Horizontal)
    QTest.qWait(30)
    destination = QPoint(minimum - 20, point.y())
    space.eventFilter(canvas, QMouseEvent(QEvent.Type.MouseMove, destination, canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert space.preview and 0 < space.preview.fraction < 0.75
    space.eventFilter(canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, destination, canvas.mapToGlobal(destination), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    QTest.qWait(220)
    assert dock.isVisible() and not space.collapsed and not space.transitions


def test_native_splitter_dispatch_resizes_before_it_parks(window):
    space, canvas = window.workspace, window.workspace.canvas
    dock = space.docks["targets"]
    point = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
    minimum = dock.minimumSizeHint().width()
    for x in range(point.x(), minimum - 110, -10):
        position = QPoint(x, point.y())
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, position, canvas.mapToGlobal(position), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.processEvents()
    assert dock.width() <= minimum + 16
    assert space.preview and space.preview.fraction >= 0.75
    assert dock.isVisible() and not space.collapsed
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(minimum - 100, point.y()))
    QTest.qWait(310)
    assert "targets" in space.collapsed and dock.isHidden()


def test_task_progress_waits_for_actual_completion_and_never_displays_premature_100(window):
    release = threading.Event()
    def work():
        window.service.report(0.6, "已完成真实步骤")
        release.wait(5)
        return [{"ok": False}]
    assert window.run(work, rescan=False)
    assert not window.run(lambda: None)
    try:
        QTest.qWait(450)
        assert 5900 <= window.progress.value() < 6000
        assert window.busy
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        QTest.qWait(30)
    assert window.task_progress.failed
    assert window.task_progress.fraction < 1
    window._animate_progress(9999)
    assert window.percent_label.text() == "99%"
    window._animate_progress(10000)
    assert window.percent_label.text() == "100%"


def test_worker_releases_busy_even_if_the_error_log_cannot_be_written(window, monkeypatch):
    monkeypatch.setattr(window.service.log, "emit", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fixture disk unavailable")))
    def fail():
        raise ValueError("fixture failure")
    window.run(fail, rescan=False)
    deadline = time.monotonic() + 3
    while window.busy and time.monotonic() < deadline:
        QTest.qWait(30)
    assert not window.busy and window.task_progress.failed


def test_expired_explicit_approval_never_switches_or_grants(window, tmp_path):
    from installer.ui.operator_dialog import OperatorCenter
    from windows_mcp.infrastructure.approvals import ApprovalStore
    store = ApprovalStore(tmp_path / "approval-fixture")
    other = store.request("synthetic-session", "Files", {"operation": "write"}, 2, "isolated fixture")
    dialog = OperatorCenter(window, store, "0" * 32)
    assert dialog.request_id == "0" * 32
    assert not dialog.approve_button.isEnabled()
    assert not store.read(other["request_id"])["approved"]
    dialog.close()
