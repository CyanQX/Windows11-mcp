"""Exercise the actual Qt widgets, dock persistence and background work lifecycle."""
from pathlib import Path
import copy
import threading
import time

import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QTimer
from PySide6.QtGui import QMouseEvent, QAccessible, QWheelEvent, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QProgressBar, QMenu, QPushButton

from installer.settings import SettingsService
from installer.models import SelectionModel
from installer.ui.dialogs import Targets, StatusDialog, Preview
from installer.ui.main_window import MainWindow
from installer.ui.controls import GlassCheckBox
from installer.ui.settings_dialog import Appearance, SettingsHub, Preferences, Backups, Updates
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
    destination = QPoint(dock.minimumSizeHint().width() - 160, point.y())
    move = QMouseEvent(QEvent.Type.MouseMove, destination, canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.workspace.eventFilter(canvas, move)
    assert dock.isVisible() and "targets" not in window.workspace.collapsed
    assert window.workspace.preview.fraction > 0.4
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, destination, canvas.mapToGlobal(destination), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    window.workspace.eventFilter(canvas, release)
    QTest.qWait(310)
    assert "targets" in window.workspace.collapsed
    handle = window.workspace.collapsed["targets"][1]
    origin = handle.control.mapToGlobal(QPoint(5, 5))
    handle.eventFilter(handle.control, QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(5, 5), origin, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    handle.eventFilter(handle.control, QMouseEvent(QEvent.Type.MouseMove, QPoint(45, 5), origin + QPoint(40, 0), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert dock.isVisible() and window.workspace.preview and "targets" in window.workspace.collapsed
    handle.eventFilter(handle.control, QMouseEvent(QEvent.Type.MouseMove, QPoint(605, 5), origin + QPoint(600, 0), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    handle.eventFilter(handle.control, QMouseEvent(QEvent.Type.MouseButtonRelease, QPoint(605, 5), origin + QPoint(600, 0), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    QTest.qWait(220)
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


def test_appearance_commits_only_when_done_without_rescanning(window):
    service, events = window.service, list(window.service.log.events)
    original = copy.deepcopy(window.settings.value)
    mode = window.theme.mode
    stylesheet = window.logs.styleSheet()
    popup = Appearance(window)
    popup.controls["theme"].setCurrentIndex(popup.controls["theme"].findData("warm"))
    popup.controls["log_background"].setCurrentIndex(popup.controls["log_background"].findData("dark"))
    assert window.theme.mode == mode and window.logs.styleSheet() == stylesheet
    assert window.settings.value == original
    popup.save()
    assert window.theme.mode == "warm" and "#18283D" in window.logs.styleSheet()
    assert window.service is service and window.service.log.events == events
    window.settings.save(original)
    window.apply_appearance()


def test_appearance_wheel_and_cancel_never_change_saved_preferences(window, monkeypatch):
    original = copy.deepcopy(window.settings.value)
    popup = Appearance(window)
    for choice in (*popup.controls.values(), popup.strength):
        choice.setFocus()
        initial = choice.currentIndex()
        event = QWheelEvent(QPointF(20, 20), QPointF(choice.mapToGlobal(QPoint(20, 20))), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(choice, event)
        assert choice.currentIndex() == initial
    monkeypatch.setattr("installer.ui.settings_dialog.QColorDialog.getColor", lambda *_: QColor("#ABCDEF"))
    popup.choose_color()
    popup.controls["theme"].setCurrentIndex(2)
    popup.strength.setCurrentIndex(0)
    popup.request_layout_reset()
    popup.close()
    QTest.qWait(250)
    assert window.settings.value == original


def test_client_folder_picker_keeps_folder_and_scans_in_background(window, tmp_path, monkeypatch):
    folder = tmp_path / "Harness"
    folder.mkdir()
    (folder / "DeepSeek Harness.exe").write_bytes(b"fixture")
    monkeypatch.setattr("installer.ui.settings_dialog.QFileDialog.getExistingDirectory", lambda *_: str(folder))
    popup = Preferences(window)
    popup.browse("exe:deepseek")
    assert popup.fields["exe:deepseek"].text() == str(folder)
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        QTest.qWait(30)
    assert not window.busy
    assert "DeepSeek Harness.exe" in popup.folder_notices["exe:deepseek"].text()
    popup.close()


def test_backup_and_restore_present_chinese_effects_with_details_collapsed(window):
    popup = Backups(window)
    labels = [item.text() for item in popup.findChildren(QLabel)]
    assert "Codex 桌面端" in labels
    assert any("安装前备份" in text for text in labels)
    assert not any("committed" in text or " · install" in text for text in labels)
    from installer.ui.controls import ChoiceBox
    assert not popup.findChildren(ChoiceBox)  # One target never needs a dropdown.
    plan = window.service.preview_restore(window.service.latest_install(), "codex")
    preview = Preview(window, "配置恢复预览", plan)
    labels = [item.text() for item in preview.findChildren(QLabel)]
    assert "移除 Windows-MCP 连接" in labels
    assert "保留当前 Skill 和其他软件配置" in labels
    assert not any(text in {"restore_config", "None", "target", "operation"} for text in labels)
    assert all(box.isHidden() for box in preview.findChildren(QPlainTextEdit))
    preview.close()
    popup.close()


def test_undo_button_is_before_copy_and_restores_completed_install(window):
    assert window.undo_button.isEnabled()
    log_toolbar = window.undo_button.parentWidget()
    names = [item.text() for item in log_toolbar.findChildren(QPushButton)]
    assert names == ["撤销", "复制", "清空"]
    transaction = window.service.latest_install()
    plan = window.service.preview_undo_install(transaction)
    assert not plan["conflicts"]
    result = window.service.undo_install(transaction, plan["digest"])
    window.accept_states(window.service.inspect_all())
    assert all(row["ok"] for row in result)
    assert not window.undo_button.isEnabled()


def test_undo_button_cancels_background_install_and_preserves_previous_client(window):
    import asyncio
    from tests.installer_helpers import Healthy
    entered, closed = threading.Event(), threading.Event()
    class Probe(Healthy):
        async def probe(self, executable):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                closed.set()
    window.service.health = Probe()
    plan = window.service.preview("install", ["claude"])
    window.start_install(["claude"], plan["digest"])
    deadline = time.monotonic() + 5
    while not entered.is_set() and time.monotonic() < deadline:
        QTest.qWait(20)
        time.sleep(0.01)  # Let the Python worker run outside QTest's nested event loop.
    if not entered.is_set():
        window.undo_install()
    assert entered.is_set() and window.busy and window.undo_button.isEnabled()
    window.undo_button.click()
    assert not window.undo_button.isEnabled()
    while window.busy and time.monotonic() < deadline:
        QTest.qWait(20)
        time.sleep(0.01)
    assert closed.is_set() and not window.busy
    assert not window.service.backend.clients["claude"].config.exists()
    assert window.service.detector.inspect("codex").receipt_valid
    assert window.service.backend.executable.exists()


def test_settings_order_and_no_external_adoption_menu(window):
    popup = SettingsHub(window)
    assert popup.names == ["外观", "高级设置", "配置与恢复", "安装目录", "检查更新", "日志与诊断", "关于"]
    popup.close()


def test_advanced_settings_groups_values_without_helper_descriptions(window):
    dialog = Preferences(window)
    QTest.qWait(50)
    assert dialog.body.contentsMargins().left() >= 24
    labels = [item.text() for item in dialog.findChildren(QLabel)]
    assert "连接与日志" in labels and "编程端程序路径" in labels
    assert not any("Codex CLI" in text for text in labels)
    assert dialog.fields["health_timeout"].width() >= 100
    editor = dialog.fields["health_timeout"].lineEdit()
    assert editor.width() >= editor.fontMetrics().horizontalAdvance(editor.displayText()) + 24
    assert dialog.fields["exe:deepseek"].accessibleName() == "DeepSeek Harness"
    dialog.close()


def test_glass_selection_keeps_keyboard_accessibility_and_disabled_protection(window):
    window.selector.toggle()
    control = window.selector.checks["deepseek"]
    assert isinstance(control, GlassCheckBox)
    control.setFocus()
    QTest.keyClick(control, Qt.Key.Key_Space)
    assert control.isChecked() and "deepseek" in window.selection.selected
    interface = QAccessible.queryAccessibleInterface(control)
    assert interface.role() == QAccessible.Role.CheckBox and interface.state().checked
    disabled = window.selector.checks["codex"]
    assert not disabled.isEnabled()
    disabled.click()
    assert not disabled.isChecked()


def test_main_and_panel_headers_have_no_helper_copy_or_permanent_layout_buttons(window):
    labels = [item.text() for item in window.findChildren(QLabel)]
    assert not any("可同时安装" in text or "Skill 提供" in text or "拖动" in text or "已接入项请" in text or "最近验证 20" in text for text in labels)
    for dock in window.workspace.docks.values():
        assert not dock.titleBarWidget().findChildren(QPushButton)
    assert window.workspace.docks["actions"].isAncestorOf(window.settings_button)
    actions = []
    def inspect_menu():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            actions.extend(item.text() for item in menu.actions())
            menu.close()
    QTimer.singleShot(60, inspect_menu)
    window.workspace.dock_menu(window.workspace.docks["targets"])
    assert "收起面板" in actions and "恢复默认布局" in actions


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
    for x in range(point.x(), minimum - 170, -10):
        position = QPoint(x, point.y())
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, position, canvas.mapToGlobal(position), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.processEvents()
    assert dock.width() <= minimum + 16
    assert space.preview and space.preview.fraction > 0.4
    assert dock.isVisible() and not space.collapsed
    assert space.docks["maintenance"].x() <= dock.geometry().right() + 13, "The native separator must release its old footprint too"
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(minimum - 160, point.y()))
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


@pytest.mark.parametrize("key,edge", [("targets", "left"), ("maintenance", "right"), ("actions", "bottom"), ("actions", "top")])
def test_slide_moves_the_whole_panel_and_releases_real_layout_space(window, key, edge):
    from installer.ui.workspace import SlidePreview
    space = window.workspace
    dock = space.docks[key]
    space.move_to(dock, edge)
    QTest.qWait(60)
    body, title = dock.widget(), dock.titleBarWidget()
    horizontal = edge in {"left", "right"}
    original = dock.width() if horizontal else dock.height()
    other = space.docks["maintenance" if key != "maintenance" else "targets"]
    other_extent = other.width() if horizontal else other.height()
    space.preview = preview = SlidePreview(space, dock, edge)
    preview.advance(.55)
    QTest.qWait(35)
    assert body.isHidden() and title.isHidden(), "The old frame/body must not stay behind the moving image"
    assert original * .35 < (dock.width() if horizontal else dock.height()) < original * .6
    assert (other.width() if horizontal else other.height()) > other_extent + original * .35
    space.clear_preview()
    QTest.qWait(35)
    assert dock.widget() is body and dock.titleBarWidget() is title
    assert not body.isHidden() and not title.isHidden() and dock.slide is None
    assert dock.maximumWidth() > 10000 and dock.maximumHeight() > 10000


def test_maximize_float_redock_restore_does_not_leave_a_giant_footer(window):
    space, dock = window.workspace, window.workspace.docks["actions"]
    original_size = window.size()
    window.showMaximized()
    QTest.qWait(120)
    dock.setFloating(True)
    dock.resize(1000, 550)
    window.showNormal()
    window.resize(original_size)
    QTest.qWait(100)
    space.move_to(dock, "bottom")
    QTest.qWait(120)
    assert dock.height() <= 180
    assert window.install_button.isVisible() and window.uninstall_button.isVisible()
    assert not space.drop_preview.isVisible()


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_floating_drag_shows_destination_and_commits_that_destination(window, edge):
    space, dock = window.workspace, window.workspace.docks["targets"]
    title = dock.titleBarWidget()
    start = title.mapToGlobal(title.rect().center())
    space.start_title_drag(dock, start)
    bounds = space.canvas.rect()
    local = {"left": QPoint(8, bounds.center().y()), "right": QPoint(bounds.right() - 8, bounds.center().y()), "top": QPoint(bounds.center().x(), 8), "bottom": QPoint(bounds.center().x(), bounds.bottom() - 8)}[edge]
    destination = space.canvas.mapToGlobal(local)
    space.drag_title(destination)
    assert dock.isFloating()
    assert space.drop_preview.isVisible()
    assert space.drop_preview.target.edge == edge
    assert bounds.contains(space.drop_preview.target.rect)
    space.finish_title_drag(destination)
    QTest.qWait(50)
    assert not dock.isFloating() and not space.drop_preview.isVisible()
    assert space.last_area[dock.key] == edge


def test_dragging_title_uses_mouse_events_and_escape_cancels_the_preview(window):
    space, dock = window.workspace, window.workspace.docks["targets"]
    title = dock.titleBarWidget()
    start = title.rect().center()
    destination = space.canvas.mapToGlobal(QPoint(space.canvas.width() - 15, 180))
    QTest.mousePress(title, Qt.MouseButton.LeftButton, pos=start)
    local = title.mapFromGlobal(destination)
    QApplication.sendEvent(title, QMouseEvent(QEvent.Type.MouseMove, local, destination, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert dock.isFloating() and space.drop_preview.isVisible()
    QTest.keyClick(title, Qt.Key.Key_Escape)
    QTest.mouseRelease(title, Qt.MouseButton.LeftButton, pos=start)
    QTest.qWait(50)
    assert not dock.isFloating() and not space.drop_preview.isVisible()
    assert space.last_area[dock.key] == "left"


def test_choice_popup_has_rounded_delegate_and_commits_only_explicit_selection(window):
    from installer.ui.controls import ChoiceDelegate
    popup = Appearance(window)
    choice = popup.controls["log_background"]
    original = choice.currentIndex()
    choice.showPopup()
    QTest.qWait(30)
    view = choice.popup.list
    assert isinstance(view.itemDelegate(), ChoiceDelegate)
    assert choice.popup.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert choice.popup.layout().contentsMargins().top() == 9
    target = choice.model().index(1, 0)
    QTest.mouseMove(view.viewport(), view.visualRect(target).center())
    assert choice.currentIndex() == original
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert not choice.popup.isVisible() and choice.currentIndex() == original
    choice.showPopup()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(target).center())
    assert choice.currentIndex() == 1 and not choice.popup.isVisible()
    assert window.settings.value["appearance"]["log_background"] != "warm"
    popup.close()


def test_edge_handle_short_pull_returns_to_edge_and_full_pull_restores_live_controls(window):
    space, dock = window.workspace, window.workspace.docks["targets"]
    body = dock.widget()
    space.collapse(dock, animate=False)
    space.pull_from_edge(dock, 35)
    assert space.preview and dock.width() <= 40
    space.finish_reveal(dock)
    QTest.qWait(310)
    assert dock.isHidden() and len(space.collapsed) == 1
    space.pull_from_edge(dock, 1000)
    space.finish_reveal(dock)
    QTest.qWait(230)
    assert dock.isVisible() and not space.collapsed and dock.widget() is body
    assert not body.isHidden() and not space.transitions and not space.preview


def test_native_divider_can_reverse_out_of_a_partial_slide_without_a_gap(window):
    space, canvas, dock = window.workspace, window.workspace.canvas, window.workspace.docks["targets"]
    point = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
    minimum = dock.minimumSizeHint().width()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
    for x in range(point.x(), minimum - 160, -8):
        destination = QPoint(x, point.y())
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, destination, canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.processEvents()
    assert space.preview and space.split_owned
    for x in range(minimum - 140, minimum + 140, 8):
        destination = QPoint(x, point.y())
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, destination, canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.processEvents()
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=destination)
    QTest.qWait(30)
    assert space.preview is None and not space.collapsed and dock.slide is None
    assert dock.width() > minimum + 30
    assert space.docks["maintenance"].x() <= dock.geometry().right() + 13


def test_reverse_drag_cannot_force_the_restored_window_wider_than_its_canvas(window):
    space, canvas, dock = window.workspace, window.workspace.canvas, window.workspace.docks["targets"]
    original = window.size()
    space.split_owned = True
    space.resize_owned_split(dock, "left", canvas.width() * 2, dock.minimumSizeHint().width())
    QTest.qWait(40)
    assert window.size() == original
    assert space.docks["maintenance"].width() >= space.docks["maintenance"].minimumSizeHint().width()
    space.release_live_split()
    space.split_owned = False


def drag_canvas_boundary(space, start, delta):
    canvas = space.canvas
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    count = max(2, (abs(delta.x()) + abs(delta.y())) // 8)
    for step in range(1, count + 1):
        point = start + QPoint(round(delta.x() * step / count), round(delta.y() * step / count))
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, point, canvas.mapToGlobal(point), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.processEvents()
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + delta)
    QTest.qWait(310)


@pytest.mark.parametrize("maximized", [False, True])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_targets_nested_above_wider_actions_can_slide_independently(window, maximized, edge):
    space = window.workspace
    dock, actions = space.docks["targets"], space.docks["actions"]
    if edge == "right":
        space.swap(dock, space.docks["maintenance"])
    space.canvas.splitDockWidget(dock, actions, Qt.Orientation.Vertical)
    if maximized:
        window.showMaximized()
    QTest.qWait(60)
    assert actions.minimumSizeHint().width() > dock.minimumSizeHint().width()
    start = QPoint(dock.geometry().right() + 5 if edge == "left" else dock.x() - 5, dock.geometry().center().y())
    assert set(space.divider_snapshot(space.canvas.mapToGlobal(start))) == {"targets", "maintenance"}
    drag_canvas_boundary(space, start, QPoint((-1 if edge == "left" else 1) * (dock.width() + 100), 0))
    assert dock.isHidden() and space.collapsed["targets"][0] == edge
    assert actions.isVisible() and "actions" not in space.collapsed and actions.height() <= 180
    assert window.settings_button.isVisible() and window.install_button.isVisible()
    space.collapsed["targets"][1].control.click()
    QTest.qWait(230)
    assert dock.isVisible() and "targets" not in space.collapsed
    assert actions.isVisible() and actions.height() <= 180, {key: (item.geometry(), item.minimumSizeHint()) for key, item in space.docks.items()}
    window.showNormal()


def test_nested_targets_partial_slide_can_reverse_without_moving_the_actions(window):
    space = window.workspace
    dock, actions = space.docks["targets"], space.docks["actions"]
    space.canvas.splitDockWidget(dock, actions, Qt.Orientation.Vertical)
    QTest.qWait(60)
    initial = actions.geometry()
    start = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
    minimum = space.divider_minimum(dock, "left").width()
    QTest.mousePress(space.canvas, Qt.MouseButton.LeftButton, pos=start)
    for x in range(start.x(), minimum - 200, -8):
        point = QPoint(x, start.y())
        QApplication.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseMove, point, space.canvas.mapToGlobal(point), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.processEvents()
    assert space.preview and space.preview.dock is dock and space.preview.shared_span
    assert abs(actions.height() - initial.height()) <= 16 and actions.width() >= actions.minimumSizeHint().width()
    destination = QPoint(start.x() + 20, start.y())
    QApplication.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseMove, destination, space.canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(space.canvas, Qt.MouseButton.LeftButton, pos=destination)
    QTest.qWait(230)
    assert not space.collapsed and space.preview is None and space.split_live is None
    assert dock.isVisible() and actions.isVisible() and actions.height() <= 180


def test_native_separator_observes_a_shared_minimum_without_matching_panel_rectangles(window, monkeypatch):
    space = window.workspace
    dock, actions = space.docks["targets"], space.docks["actions"]
    space.canvas.splitDockWidget(dock, actions, Qt.Orientation.Vertical)
    QTest.qWait(60)
    monkeypatch.setattr(space, "aligned_docks", lambda *args: [])
    start = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
    drag_canvas_boundary(space, start, QPoint(-dock.width() - 100, 0))
    assert dock.isHidden() and "targets" in space.collapsed
    assert actions.isVisible() and actions.height() <= 180
    space.collapsed["targets"][1].control.click()
    QTest.qWait(230)
    assert dock.isVisible() and dock.x() >= 0 and actions.height() <= 180


def test_automatic_update_hint_refreshes_open_menu_and_waits_for_user_click(window, monkeypatch):
    window.update_release = None
    hub = SettingsHub(window)
    assert not hub.update_badge.isVisible()
    release = {"available": True, "current": window.version, "latest": "99.0.0", "notes": "新的更新", "kind": "installer"}
    window.events.put(("update_hint", release))
    window.poll()
    assert hub.update_badge.isVisible()
    hub.close()
    calls = []
    monkeypatch.setattr(window, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    dialog = Updates(window)
    assert not calls and dialog.download_button.isEnabled()
    assert "99.0.0" in dialog.notice.text()
    dialog.download_button.click()
    assert len(calls) == 1 and "下载" in calls[0][1]["title"]
    dialog.close()
    window.update_release = None


@pytest.mark.parametrize("kind", ["installer", "portable"])
def test_user_requested_update_launches_without_another_wizard_preview(window, monkeypatch, tmp_path, kind):
    from installer.ui import settings_dialog
    window.update_release = {"available": True, "current": window.version, "latest": "99.0.0", "kind": kind}
    dialog = Updates(window)
    path = tmp_path / "next.exe"
    calls = []
    monkeypatch.setattr(settings_dialog.sys, "frozen", True, raising=False)
    monkeypatch.setattr(window, "close", lambda: calls.append("close"))
    monkeypatch.setattr(dialog.updater, "launch_installer", lambda *args: calls.append("installer"))
    monkeypatch.setattr(dialog.updater, "prepare", lambda *args: (tmp_path / "plan.json", "nonce"))
    monkeypatch.setattr(settings_dialog, "start_update", lambda *args: calls.append("portable"))
    dialog.downloaded(path)
    assert calls == [kind, "close"]
    assert not any(isinstance(item, Preview) for item in window.dialogs)
    dialog.close()
    window.update_release = None


def test_clear_all_selection_keeps_popup_and_does_not_change_installed_files(window):
    selector, service = window.selector, window.service
    profile = service.backend.clients["codex"]
    files = [profile.config, profile.skill_root / "windows-mcp/SKILL.md", service.backend.receipt_path(profile)]
    before = {path: path.read_bytes() for path in files}
    selector.toggle()
    popup = selector.popup
    selector.select_all()
    buttons = popup.findChildren(QPushButton)
    names = [control.text() for control in buttons]
    assert names.index("清除所选") == names.index("全选可安装项") + 1
    assert selector.clear_button.isEnabled() and window.install_button.isEnabled()
    selector.clear_button.click()
    assert not window.selection.selected and not window.install_button.isEnabled()
    assert selector.popup is popup and popup.isVisible() and not selector.clear_button.isEnabled()
    assert all(not checkbox.isChecked() for checkbox in selector.checks.values())
    assert not selector.checks["codex"].isEnabled()
    window.accept_states(service.inspect_all())
    assert not window.selection.selected and {path: path.read_bytes() for path in files} == before


@pytest.mark.parametrize("minimize", [False, True])
def test_nested_footer_maximized_resize_then_restore_can_shrink_and_park(window, minimize):
    space, dock = window.workspace, window.workspace.docks["actions"]
    original = window.size()
    try:
        space.canvas.splitDockWidget(space.docks["targets"], dock, Qt.Orientation.Vertical)
        QTest.qWait(60)
        assert space.last_area["actions"] == "left"
        window.showMaximized()
        QTest.qWait(100)
        start = QPoint(dock.geometry().center().x(), dock.y() - 5)
        assert space.divider_snapshot(space.canvas.mapToGlobal(start))["actions"][1] == "bottom"
        drag_canvas_boundary(space, start, QPoint(0, -230))
        assert space.maximized_footer_height is not None
        if minimize:
            window.showMinimized()
            QTest.qWait(50)
        window.showNormal()
        window.resize(original)
        QTest.qWait(100)
        assert dock.height() <= 180
        start = QPoint(dock.geometry().center().x(), dock.y() - 5)
        drag_canvas_boundary(space, start, QPoint(0, dock.height() + 100))
        assert dock.isHidden() and space.collapsed["actions"][0] == "bottom"
        space.collapsed["actions"][1].control.click()
        QTest.qWait(230)
        assert dock.isVisible() and dock.height() <= 180
        assert window.install_button.height() >= 44 and window.settings_button.height() >= 44
        assert window.install_button.width() >= window.install_button.sizeHint().width()
    finally:
        window.showNormal()
        window.resize(original)
        space.reset()


@pytest.mark.parametrize("edge", ["top", "bottom"])
def test_floating_footer_after_restore_can_resize_past_minimum_and_park(window, edge):
    space, dock = window.workspace, window.workspace.docks["actions"]
    original = window.size()
    try:
        window.showMaximized()
        QTest.qWait(50)
        dock.setFloating(True)
        QTest.qWait(40)
        dock.resize(580, 400)
        window.showNormal()
        window.resize(original)
        QTest.qWait(80)
        assert dock.height() <= 180 and dock.minimumHeight() <= dock.minimumSizeHint().height()
        start = QPoint(dock.width() // 2, 1 if edge == "top" else dock.height() - 1)
        global_start = dock.mapToGlobal(start)
        QTest.mousePress(dock, Qt.MouseButton.LeftButton, pos=start)
        assert space.floating_resize is not None
        direction = 1 if edge == "top" else -1
        for distance in range(0, 240, 8):
            global_point = global_start + QPoint(0, distance * direction)
            point = space.canvas.mapFromGlobal(global_point)
            QApplication.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseMove, point, global_point, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QApplication.processEvents()
        assert space.preview and space.preview.fraction > .4
        QApplication.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, point, global_point, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        QTest.qWait(310)
        assert dock.isHidden() and "actions" in space.collapsed
        space.collapsed["actions"][1].control.click()
        QTest.qWait(50)
        assert dock.isFloating() and dock.isVisible() and dock.height() <= 180
        assert window.install_button.isVisible()
        assert window.install_button.height() >= 44
        assert window.install_button.width() >= window.install_button.sizeHint().width()
    finally:
        window.showNormal()
        window.resize(original)
        space.reset()


def test_restoring_window_during_a_resize_releases_temporary_size_constraints(window):
    space, dock = window.workspace, window.workspace.docks["actions"]
    original = window.size()
    try:
        space.canvas.splitDockWidget(space.docks["targets"], dock, Qt.Orientation.Vertical)
        window.showMaximized()
        QTest.qWait(80)
        point = QPoint(dock.geometry().center().x(), dock.y() - 5)
        QTest.mousePress(space.canvas, Qt.MouseButton.LeftButton, pos=point)
        for offset in range(0, dock.height() + 30, 8):
            destination = point + QPoint(0, offset)
            QApplication.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseMove, destination, space.canvas.mapToGlobal(destination), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QApplication.processEvents()
        window.showNormal()
        window.resize(original)
        QTest.qWait(100)
        assert space.press is None and space.preview is None and space.split_live is None
        assert dock.minimumHeight() <= dock.minimumSizeHint().height()
        assert dock.height() <= 180
        drag_canvas_boundary(space, QPoint(dock.geometry().center().x(), dock.y() - 5), QPoint(0, 250))
        assert "actions" in space.collapsed
    finally:
        window.showNormal()
        window.resize(original)
        space.reset()


def test_window_state_change_finishes_edge_animation_without_leaking_fixed_sizes(window):
    space, dock = window.workspace, window.workspace.docks["actions"]
    original = window.size()
    try:
        space.canvas.splitDockWidget(space.docks["targets"], dock, Qt.Orientation.Vertical)
        space.collapse(dock)
        assert "actions" in space.transitions
        window.showMaximized()
        QTest.qWait(50)
        assert dock.isHidden() and not space.transitions
        space.restore(dock, animate=True)
        window.showNormal()
        window.resize(original)
        QTest.qWait(80)
        assert not space.transitions and dock.slide is None
        assert dock.height() <= 180 and dock.minimumHeight() <= dock.minimumSizeHint().height()
        drag_canvas_boundary(space, QPoint(dock.geometry().center().x(), dock.y() - 5), QPoint(0, 250))
        assert "actions" in space.collapsed
    finally:
        window.showNormal()
        window.resize(original)
        space.reset()


def test_legacy_nested_layout_does_not_reapply_a_maximized_footer_ratio(window):
    space, dock = window.workspace, window.workspace.docks["actions"]
    space.canvas.splitDockWidget(space.docks["targets"], dock, Qt.Orientation.Vertical)
    space.footer_height = 340
    space.normalize_layout()
    QTest.qWait(40)
    saved = space.snapshot()
    saved.pop("footer_height")
    saved.pop("maximized_footer_height")
    saved["footer_ratio"] = .42
    space.reset()
    space.load(saved)
    QTest.qWait(80)
    assert space.last_area["actions"] == "left" and dock.height() <= 180
    drag_canvas_boundary(space, QPoint(dock.geometry().center().x(), dock.y() - 5), QPoint(0, 250))
    assert "actions" in space.collapsed
