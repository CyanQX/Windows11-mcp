"""Exercise fixed panels, real widget interactions and background work lifecycle."""
from pathlib import Path
import copy
import threading
import time

import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QUrl
from PySide6.QtGui import QAccessible, QWheelEvent, QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QProgressBar, QPushButton

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
    if window.selector.popup:
        window.selector.popup.close()
    window.close()
    app.processEvents()


@pytest.fixture
def window(native_window, tmp_path, monkeypatch):
    from installer.detection import ClientDiscovery
    from installer.models import Presence
    class IsolatedDiscovery(ClientDiscovery):
        def detect(self, profile):
            return super().detect(profile) if self.overrides.get(profile.id) else (Presence.NOT_FOUND, None)
    monkeypatch.setattr(native_window.settings, "discovery", lambda: IsolatedDiscovery(tmp_path))
    for dialog in list(native_window.dialogs):
        dialog.close()
    if native_window.selector.popup:
        native_window.selector.popup.close()
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    native_window.service = service
    from windows_mcp.infrastructure.approvals import ApprovalStore
    native_window.approval_store = ApprovalStore(tmp_path / "manager-approvals")
    native_window.known_requests = set()
    native_window.pending_approvals = 0
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


def test_client_dropdown_second_click_closes_and_next_click_reopens(window):
    trigger = window.selector.trigger
    QTest.mouseClick(trigger, Qt.MouseButton.LeftButton)
    assert window.selector.popup is not None and window.selector.popup.isVisible()
    QTest.mouseClick(trigger, Qt.MouseButton.LeftButton)
    assert window.selector.popup is None
    QTest.mouseClick(trigger, Qt.MouseButton.LeftButton)
    assert window.selector.popup is not None and window.selector.popup.isVisible()


def test_client_popup_is_centered_scrollable_and_marks_installed_clients(window):
    original = window.pos()
    window.move(window.screen().availableGeometry().topLeft() + QPoint(300, 80))
    try:
        window.selector.toggle()
        QTest.qWait(40)
        popup, trigger = window.selector.popup, window.selector.trigger
        assert abs(popup.geometry().center().x() - trigger.mapToGlobal(trigger.rect().center()).x()) <= 1
        assert window.selector.scroll.verticalScrollBar().maximum() > 0
        assert "opencode" not in window.selector.checks
        assert {"zcode", "trae"} <= window.selector.checks.keys()
        installed = window.selector.checks["codex"]
        assert installed.property("installed") and not installed.isChecked() and not installed.isEnabled()
        window.selector.scroll.verticalScrollBar().setValue(window.selector.scroll.verticalScrollBar().maximum())
        assert window.selector.scroll.verticalScrollBar().value() > 0
    finally:
        if window.selector.popup:
            window.selector.popup.close()
        window.move(original)


def test_details_fit_short_content_and_keep_wheel_inside_the_detail(window):
    from installer.ui.dialogs import Preview
    from PySide6.QtWidgets import QScrollArea

    paths = Preview(window, "实际安装目录", {"Claude Code": {"skill": "C:/example/skills", "mcp": "C:/example/config.json", "custom": False}})
    control = next(item for item in paths.findChildren(QPushButton) if "展开详情" in item.text())
    control.click()
    QTest.qWait(20)
    short = paths.findChild(QPlainTextEdit)
    assert short.isVisible() and short.height() < 160
    assert short.verticalScrollBar().maximum() == 0
    paths.close()
    dialog = StatusDialog(window)
    next(item for item in dialog.findChildren(QPushButton) if item.text() == "详情").click()
    QTest.qWait(20)
    controls = [item for item in dialog.findChildren(QPushButton) if item.text() == "详情"]
    assert all(item.height() >= 44 for item in controls)
    outer = dialog.findChild(QScrollArea).verticalScrollBar()
    box = next(item for item in dialog.findChildren(QPlainTextEdit) if item.isVisible())
    for delta in (120, -120):
        box.verticalScrollBar().setValue(0 if delta > 0 else box.verticalScrollBar().maximum())
        outer.setValue(outer.maximum() // 2)
        before = outer.value()
        position = box.viewport().rect().center()
        event = QWheelEvent(QPointF(position), QPointF(box.viewport().mapToGlobal(position)), QPoint(), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(box.viewport(), event)
        assert event.isAccepted() and outer.value() == before
    dialog.close()


def test_status_shows_readable_facts_and_collapses_raw_details(window):
    dialog = StatusDialog(window)
    assert any(item.text().startswith("已安装 ") for item in dialog.findChildren(QLabel))
    assert not any("MCP 配置" in item.text() or "客户端  " in item.text() for item in dialog.findChildren(QLabel))
    assert any(item.text() == "详情" for item in dialog.findChildren(QPushButton))
    details = dialog.findChildren(QPlainTextEdit)
    assert details and all(item.isHidden() for item in details)
    dialog.close()


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
    popup.close()
    QTest.qWait(250)
    assert window.settings.value == original


def test_client_folder_picker_keeps_real_folder_for_explicit_background_detection(window, tmp_path, monkeypatch):
    folder = tmp_path / "Harness"
    folder.mkdir()
    (folder / "DeepSeek Harness.exe").write_bytes(b"fixture")
    monkeypatch.setattr("installer.ui.settings_dialog.QFileDialog.getExistingDirectory", lambda *_: str(folder))
    popup = Preferences(window)
    QTest.qWait(50)
    popup.browse("exe:deepseek")
    popup.detect_button.click()
    assert popup.fields["exe:deepseek"].text() == str(folder)
    deadline = time.monotonic() + 5
    while popup.folder_notices["exe:deepseek"].text() == "正在检测…" and time.monotonic() < deadline:
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
    assert popup.names == ["外观", "高级设置", "操作审批", "配置与恢复", "安装目录", "检查更新", "日志与诊断"]
    popup.close()


def test_trust_center_reflects_grant_and_revoke(window, tmp_path):
    from installer.ui.operator_dialog import TrustCenter
    from windows_mcp.infrastructure.approvals import ApprovalStore

    window.approval_store = ApprovalStore(tmp_path / "approvals")
    try:
        dialog = TrustCenter(window)
        QTest.qWait(30)
        assert "每次操作都需要批准" in dialog.state.text()
        dialog.confirm.setChecked(False)
        dialog.grant()
        assert "勾选" in dialog.notice.text()

        dialog.store.grant("forever")
        dialog.refresh()
        assert "已完全授权" in dialog.state.text() and dialog.revoke_button.isEnabled()
        dialog.store.revoke()
        dialog.refresh()
        assert "每次操作都需要批准" in dialog.state.text()
        dialog.close()
    finally:
        window.approval_store = None


def test_top_navigation_is_transparent_and_stays_above_fixed_panels(window):
    assert window.top_bar.geometry().bottom() < window.workspace.geometry().top()
    assert window.settings_button.geometry().right() < window.about_button.geometry().left()
    assert window.settings_button.isVisible() and window.about_button.isVisible()
    assert window.settings_button.property("transparent") and window.about_button.property("transparent")
    window.about_button.click()
    about = window.dialogs[-1]
    assert about.windowTitle() == "关于 Windows MCP"
    assert any(item.text() == window.version for item in about.findChildren(QLabel))
    about.close()


@pytest.mark.parametrize("delta", [QPoint(-180, 0), QPoint(180, 0), QPoint(0, -180), QPoint(0, 180)])
def test_fixed_panels_ignore_title_and_boundary_drags_and_old_layout(window, delta):
    from PySide6.QtWidgets import QDockWidget, QSplitter
    space = window.workspace
    assert not space.findChildren(QDockWidget) and not space.findChildren(QSplitter)
    initial = {key: panel.geometry() for key, panel in space.panels.items()}
    for panel in space.panels.values():
        for target, start in ((panel.heading, panel.heading.rect().center()), (space, QPoint(panel.x() + panel.width() + 5, panel.y() + 80))):
            end = start + delta
            QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=start)
            QApplication.sendEvent(target, QMouseEvent(QEvent.Type.MouseMove, end, target.mapToGlobal(end), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=end)
    space.load({"state": "legacy native dock state", "collapsed": {"targets": "left"}})
    assert all(panel.isVisible() and panel.geometry() == initial[key] for key, panel in space.panels.items())
    assert space.snapshot() == {"schema": 2, "mode": "fixed"}
    original_size = window.size()
    window.resize(1000, 680)
    QTest.qWait(30)
    left, right, actions = (space.panels[key].geometry() for key in ("targets", "maintenance", "actions"))
    assert left.top() == right.top() and left.bottom() == actions.bottom()
    assert left.right() < right.left() and actions.left() == right.left()
    assert right.bottom() < actions.top() and actions.height() == 150
    window.resize(original_size)


@pytest.mark.parametrize("mode", ["light", "warm", "dark"])
@pytest.mark.parametrize("strength", [0, 65])
def test_navigation_paints_no_frame_or_dark_background_in_pointer_states(window, mode, strength):
    from installer.ui.controls import NavigationButton
    original = copy.deepcopy(window.settings.value)
    window.settings.value["theme"] = mode
    window.settings.value["appearance"]["glass_strength"] = strength
    window.apply_appearance()
    clicks = []
    control = NavigationButton("设置", lambda: clicks.append(True), window.theme.icon("settings"))
    control.resize(control.sizeHint())
    control.show()
    QTest.qWait(30)
    control.clearFocus()
    before = control.grab().toImage()
    try:
        QTest.mouseMove(control, control.rect().center())
        QTest.mousePress(control, Qt.MouseButton.LeftButton)
        assert control.isDown() and control.grab().toImage() == before
        QTest.mouseRelease(control, Qt.MouseButton.LeftButton)
        assert clicks == [True] and control.grab().toImage() == before
        QTest.keyClick(control, Qt.Key.Key_Space)
        assert clicks == [True, True]
    finally:
        control.close()
        window.settings.value = original
        window.apply_appearance()


@pytest.mark.parametrize("key", ["theme", "log_background", "strength"])
def test_appearance_choices_toggle_on_repeated_trigger_clicks(window, key):
    dialog = Appearance(window)
    choice = dialog.strength if key == "strength" else dialog.controls[key]
    QTest.qWait(30)
    original = choice.currentIndex()
    for expected in (True, False, True, False):
        QTest.mouseClick(choice, Qt.MouseButton.LeftButton)
        QTest.qWait(20)
        assert choice.popup.isVisible() == expected
        assert choice.currentIndex() == original
    dialog.close()


def test_numeric_preferences_cannot_select_text_but_step_buttons_work(window):
    dialog = Preferences(window)
    QTest.qWait(30)
    for key, title in (("health_timeout", "连接检查超时（秒）"), ("log_retention_days", "日志保留天数"), ("log_max_mb", "日志总容量上限（MB）")):
        value = dialog.fields[key]
        original = value.value()
        QTest.mouseDClick(value, Qt.MouseButton.LeftButton)
        QTest.mousePress(value, Qt.MouseButton.LeftButton, pos=QPoint(10, 15))
        QTest.mouseMove(value, QPoint(value.width() - 10, 15))
        QTest.mouseRelease(value, Qt.MouseButton.LeftButton, pos=QPoint(value.width() - 10, 15))
        assert value.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
        assert not value.hasSelectedText() and value.value() == original
        next(item for item in dialog.findChildren(QPushButton) if item.accessibleName() == "增加" + title).click()
        assert value.value() == original + 1
        next(item for item in dialog.findChildren(QPushButton) if item.accessibleName() == "减少" + title).click()
        assert value.value() == original
    dialog.close()


def test_preferences_detects_existing_programs_without_saving_and_reports_absence(window, tmp_path, monkeypatch):
    from installer.models import Presence
    from installer.detection import ClientDiscovery
    executable = tmp_path / "Trae.exe"
    executable.write_bytes(b"fixture, never executed")
    class Discovery(ClientDiscovery):
        def detect(self, profile):
            return (Presence.DETECTED, str(executable)) if profile.id == "trae" else (Presence.NOT_FOUND, None)
    monkeypatch.setattr(window.settings, "discovery", lambda: Discovery(tmp_path))
    original = copy.deepcopy(window.settings.value)
    dialog = Preferences(window)
    deadline = time.monotonic() + 3
    while not dialog.fields["exe:trae"].text() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert dialog.fields["exe:trae"].text() == str(executable)
    assert dialog.folder_notices["exe:zcode"].text() == "未安装，无法检测"
    assert window.settings.value == original and not window.busy
    dialog.fields["exe:trae"].setText("user-edited-draft")
    generation = dialog.detection_generations["exe:trae"]
    dialog.detected({"exe:trae": (generation, "", str(executable), "late result")})
    assert dialog.fields["exe:trae"].text() == "user-edited-draft"
    dialog.close()


def test_about_has_six_requested_pages_and_opens_bundled_licenses(window):
    from installer.ui.settings_dialog import About, POLICIES
    dialog = About(window)
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [name for name, _ in POLICIES]
    assert list(dialog.documents) == ["关于软件", "使用政策", "隐私政策", "许可证", "第三方声明", "AI使用免责声明"]
    for title, _ in POLICIES:
        text = dialog.documents[title].toPlainText()
        assert "jingyanx2@gmail.com" in text and "2026 年 10 月 24 日" in text
        assert len(text) > (400 if title == "关于软件" else 1000) and "CyanQX / Windows-MCP" in text
        assert "{{VERSION}}" not in text
    privacy = dialog.documents["隐私政策"].toPlainText()
    assert all(phrase in privacy for phrase in ("默认关闭", "境外", "撤回", "删除", "DPAPI", "30 天", "50 MB", "AI 客户端"))
    ai = dialog.documents["AI使用免责声明"].toPlainText()
    assert all(phrase in ai for phrase in ("显式", "隐式", "不免除", "人身损害", "重大过失", "过错", "人工智能生成合成内容标识办法"))
    for url, required in (("license:mit", "Permission is hereby granted"), ("license:qt", "GNU LESSER GENERAL PUBLIC LICENSE"), ("license:thirdparty", "Declared license")):
        dialog.open_reference(QUrl(url))
        notice = window.dialogs[-1]
        from PySide6.QtWidgets import QTextBrowser
        assert required in notice.findChild(QTextBrowser).toPlainText()
        notice.close()
    dialog.close()


def test_settings_notification_dot_is_on_the_right_and_preserves_row_alignment(window):
    popup = SettingsHub(window)
    QTest.qWait(30)
    rectangles = [control.geometry() for control in popup.controls.values()]
    assert len({rect.left() for rect in rectangles}) == 1
    assert len({rect.width() for rect in rectangles}) == 1
    assert all(control.content_alignment == Qt.AlignmentFlag.AlignLeft for control in popup.controls.values())
    original = popup.size(), [control.geometry() for control in popup.controls.values()]
    popup.release_hint({"available": True})
    control = popup.controls["检查更新"]
    raster = control.grab().toImage()
    red = [(x, y) for y in range(raster.height()) for x in range(raster.width())
           if (color := raster.pixelColor(x, y)).red() > 170 and color.green() < 110 and color.blue() < 130]
    assert red and min(x for x, _ in red) > raster.width() * .8
    popup.release_hint({"available": False})
    assert not control.notification_dot
    assert (popup.size(), [item.geometry() for item in popup.controls.values()]) == original
    popup.close()


def test_settings_approval_entry_opens_defaults_before_any_request(window, monkeypatch):
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.mark_operator_window", lambda _: None)
    popup = SettingsHub(window)
    popup.controls["操作审批"].click()
    dialog = window.operator_window
    assert dialog is not None and dialog.isVisible()
    assert dialog.defaults_row.isVisible() and dialog.auto_approve.isEnabled()
    assert not dialog.auto_approve.isChecked()
    assert window.approval_store.pending() == [] and not dialog.trust.allows()
    dialog.close()


def test_plain_settings_keep_left_alignment_and_update_dot(window):
    previous = window.settings.value["appearance"]["glass_strength"]
    window.settings.value["appearance"]["glass_strength"] = 0
    window.apply_appearance()
    popup = SettingsHub(window)
    try:
        popup.release_hint({"available": True})
        control = popup.controls["检查更新"]
        image = control.grab().toImage()
        assert control.content_alignment == Qt.AlignmentFlag.AlignLeft
        assert any((color := image.pixelColor(x, y)).red() > 170 and color.green() < 110
                   for x in range(image.width() * 4 // 5, image.width()) for y in range(image.height()))
    finally:
        popup.close()
        window.settings.value["appearance"]["glass_strength"] = previous
        window.apply_appearance()


def test_default_automatic_can_be_saved_before_requests_and_switched_off(window, monkeypatch):
    from installer.ui.operator_dialog import OperatorCenter
    from PySide6.QtWidgets import QMessageBox
    from windows_mcp.infrastructure.policy import PolicyBlocked, PolicyGate

    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.mark_operator_window", lambda _: None)
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.operator_review_active", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    store = window.approval_store
    dialog = OperatorCenter(window, store)
    assert dialog.auto_approve.isEnabled() and dialog.save_default_button.isEnabled()
    dialog.auto_approve.setChecked(True)
    assert not dialog.trust.allows()
    dialog.save_default_button.click()
    gate = PolicyGate(approvals=store)
    assert gate.trust.status()["operator"] == "approval-settings"
    assert store.pending() == [] and not dialog.isVisible()
    assert gate.authorize("future-session", "Click", {"loc": [100, 200]}) == 2
    assert gate.authorize("future-session", "Type", {"text": "next"}) == 2
    reopened = OperatorCenter(window, store)
    assert reopened.auto_approve.isChecked()
    assert not reopened.auto_approve.isEnabled() and not reopened.save_default_button.isEnabled()
    QTest.mouseClick(reopened.auto_approve, Qt.MouseButton.LeftButton)
    assert reopened.auto_approve.isChecked() and gate.trust.allows()
    from installer.ui.operator_dialog import TrustCenter
    trust = TrustCenter(window)
    trust.revoke_button.click()
    reopened.refresh()
    assert not reopened.auto_approve.isChecked() and reopened.auto_approve.isEnabled()
    trust.close()
    reopened.close()
    assert not gate.trust.allows()
    with pytest.raises(PolicyBlocked):
        gate.authorize("future-session", "Click", {"loc": [100, 200]})


def test_cancelling_default_approval_does_not_grant_or_approve_pending(window, monkeypatch):
    from installer.ui.operator_dialog import OperatorCenter
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.mark_operator_window", lambda _: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    store = window.approval_store
    request = store.request("fixture-session", "Click", {"loc": [100, 200]}, 2, "isolated click")
    dialog = OperatorCenter(window, store)
    dialog.auto_approve.setChecked(True)
    dialog.save_default_button.click()
    assert not dialog.trust.allows()
    assert not store.read(request["request_id"])["approved"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.save_default_button.click()
    assert dialog.trust.allows()
    assert not store.read(request["request_id"])["approved"]


@pytest.fixture
def operator_dialog(window, tmp_path, monkeypatch):
    from installer.ui.operator_dialog import OperatorCenter
    from windows_mcp.infrastructure.approvals import ApprovalStore
    from windows_mcp.infrastructure.policy import PolicyGate

    # Exercise real widgets and signed temporary records without depending on
    # another application's live human-review window or sending desktop input.
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.mark_operator_window", lambda _: None)
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.operator_review_active", lambda: False)
    store = ApprovalStore(tmp_path / "approval-checkbox")
    request = store.request("fixture-session", "Click", {"loc": [100, 200]}, 2, "isolated click")
    dialog = OperatorCenter(window, store, request["request_id"])
    gate = PolicyGate(approvals=store)
    yield dialog, gate, request
    dialog.close()


def test_operator_unchecked_approval_is_single_use(operator_dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from windows_mcp.infrastructure.policy import PolicyBlocked

    dialog, gate, _ = operator_dialog
    assert not dialog.auto_approve.isChecked()
    assert dialog.approve_button.text() == "批准一次"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.approve_button.click()
    assert not gate.trust.allows()
    assert gate.authorize("fixture-session", "Click", {"loc": [100, 200]}) == 2
    with pytest.raises(PolicyBlocked):
        gate.authorize("fixture-session", "Click", {"loc": [100, 200]})


def test_operator_automatic_approval_continues_across_calls_and_restarts(operator_dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from windows_mcp.infrastructure.policy import PolicyBlocked, PolicyGate

    dialog, gate, _ = operator_dialog
    dialog.auto_approve.setChecked(True)
    assert dialog.approve_button.text() == "批准并开启全自动"
    assert not gate.trust.allows()  # Selection alone never grants access.
    prompts = []

    def confirm(*args):
        prompts.append(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    dialog.approve_button.click()
    assert len(prompts) == 1 and "永久有效" in prompts[0]
    assert gate.trust.status()["mode"] == "forever"
    assert gate.authorize("fixture-session", "Click", {"loc": [100, 200]}) == 2
    assert gate.authorize("next-session", "Type", {"text": "next action"}) == 2
    restarted = PolicyGate(approvals=gate.approvals)
    assert restarted.authorize("reconnected", "Shortcut", {"shortcut": "down"}) == 2
    with pytest.raises(PolicyBlocked, match="R3"):
        restarted.authorize("reconnected", "PowerShell", {"command": "Get-Process"})
    restarted.trust.revoke()
    with pytest.raises(PolicyBlocked):
        gate.authorize("fixture-session", "Type", {"text": "after revocation"})


@pytest.mark.parametrize("decision,answer", [(True, False), (False, True)])
def test_operator_cancel_or_reject_never_enables_automatic(operator_dialog, monkeypatch, decision, answer):
    from PySide6.QtWidgets import QMessageBox

    dialog, gate, _ = operator_dialog
    dialog.auto_approve.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes if answer else QMessageBox.StandardButton.No)
    dialog.decide(decision)
    assert not gate.trust.allows()
    assert not gate.approvals.consume("fixture-session", "Click", {"loc": [100, 200]})


def test_operator_refresh_preserves_checkbox_without_granting(operator_dialog):
    dialog, gate, _ = operator_dialog
    dialog.auto_approve.setChecked(True)
    dialog.refresh()
    assert dialog.auto_approve.isChecked()
    assert dialog.approve_button.text() == "批准并开启全自动"
    assert not gate.trust.allows()
    dialog.auto_approve.setChecked(False)
    dialog.refresh()
    assert not dialog.auto_approve.isChecked()
    assert dialog.approve_button.text() == "批准一次"


def test_operator_cannot_uncheck_active_trust_or_revoke_while_approving(operator_dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dialog, gate, _ = operator_dialog
    gate.trust.grant("forever")
    dialog.refresh()
    assert dialog.auto_approve.isChecked() and not dialog.auto_approve.isEnabled()
    QTest.mouseClick(dialog.auto_approve, Qt.MouseButton.LeftButton)
    assert dialog.auto_approve.isChecked()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.decide(True)
    assert gate.trust.allows()
    assert gate.approvals.consume("fixture-session", "Click", {"loc": [100, 200]})


def test_operator_expiry_during_confirmation_never_grants_trust(operator_dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dialog, gate, request = operator_dialog
    dialog.auto_approve.setChecked(True)
    errors = []
    monkeypatch.setattr(dialog.owner, "error", lambda *args: errors.append(args))

    def confirm(*args):
        monkeypatch.setattr("windows_mcp.infrastructure.approvals.time.time", lambda: request["expires_at"] + 1)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    dialog.decide(True)
    assert errors and not gate.trust.allows()
    assert not dialog.auto_approve.isEnabled()
    assert not dialog.approve_button.isEnabled()


def test_operator_r3_request_cannot_enable_automatic(operator_dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dialog, gate, _ = operator_dialog
    request = gate.approvals.request("fixture-session", "PowerShell", {"command": "Get-Process"}, 3, "isolated R3")
    dialog.request_id = request["request_id"]
    dialog.refresh()
    assert not dialog.auto_approve.isEnabled()
    dialog.auto_approve.setChecked(True)
    assert dialog.approve_button.text() == "批准一次"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.decide(True)
    assert not gate.trust.allows()
    assert gate.approvals.consume("fixture-session", "PowerShell", {"command": "Get-Process"})


def test_advanced_settings_groups_values_without_helper_descriptions(window):
    dialog = Preferences(window)
    QTest.qWait(50)
    assert dialog.body.contentsMargins().left() >= 24
    labels = [item.text() for item in dialog.findChildren(QLabel)]
    assert "连接与日志" in labels and "编程端程序路径" in labels
    assert not any("Codex CLI" in text for text in labels)
    assert dialog.fields["health_timeout"].width() >= 100
    editor = dialog.fields["health_timeout"]
    assert editor.width() >= editor.fontMetrics().horizontalAdvance(editor.text()) + 24
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
    for panel in window.workspace.panels.values():
        assert not panel.heading.findChildren(QPushButton)
    assert window.top_bar.isAncestorOf(window.settings_button)
    assert window.top_bar.isAncestorOf(window.about_button)
    assert not window.workspace.panels["actions"].isAncestorOf(window.settings_button)


def test_no_duplicate_brand_banner_and_footer_contains_only_action_controls(window):
    assert not any(item.text() == "Windows MCP" for item in window.findChildren(QLabel))
    assert not window.workspace.panels["actions"].findChildren(QProgressBar)
    assert window.settings_button.isVisible() and window.about_button.isVisible()
    footer_buttons = {item.text() for item in window.workspace.panels["actions"].content.findChildren(QPushButton)}
    assert footer_buttons == {"安装 Skill + MCP", "卸载 Skill + MCP"}


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
    dock = window.workspace.panels["maintenance"]
    dock.hide()
    assert not strip.content.isVisible() and not strip.content.isHidden()
    strip.complete(True)
    assert strip.delay.isActive()
    strip.delay.start(35)
    QTest.qWait(420)
    dock.show()
    assert strip.content.isHidden()


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


def test_automatic_update_hint_refreshes_open_menu_and_waits_for_user_click(window, monkeypatch):
    window.update_release = None
    hub = SettingsHub(window)
    assert not hub.controls["检查更新"].notification_dot
    original = hub.size(), {name: item.geometry() for name, item in hub.controls.items()}
    release = {"available": True, "current": window.version, "latest": "99.0.0", "notes": "新的更新", "kind": "installer"}
    window.events.put(("update_hint", release))
    window.poll()
    assert hub.controls["检查更新"].notification_dot
    assert (hub.size(), {name: item.geometry() for name, item in hub.controls.items()}) == original
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
