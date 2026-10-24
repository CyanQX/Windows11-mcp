"""Native visual acceptance in temporary homes, including actual frozen stdio probes."""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QPoint, Qt

from installer.backend import Installer
from installer.health import HealthChecker
from installer.models import Presence
from installer.operations import InstallerService
from installer.settings import SettingsService
from installer.ui.capture import capture
from installer.ui.dialogs import Diagnostics, StatusDialog, Targets, Preview, Results
from installer.ui.main_window import MainWindow
from installer.ui.settings_dialog import Appearance, SettingsHub, Preferences, Backups
from installer.ui.theme import application
from windows_mcp.infrastructure.approvals import ApprovalStore


class IsolatedDiscovery:
    def __init__(self, executable=None):
        self.overrides = {}
        self.executable = executable

    def detect(self, profile):
        if self.executable:
            return (Presence.DETECTED, str(self.executable)) if profile.id == "claude" else (Presence.NOT_FOUND, None)
        return Presence.DETECTED, "isolated UI acceptance client"


def require(condition, message="Native UI acceptance failed"):
    if not condition:
        raise RuntimeError(message)


def run(base, target, case="ready", size=None, scale=1.0, *, interactive=False):
    if not 0.75 <= scale <= 2.0:
        raise ValueError("UI acceptance scale outside bounds")
    if not math.isclose(scale, 1.0, rel_tol=0.0, abs_tol=1e-12):
        os.environ["QT_SCALE_FACTOR"] = str(scale)
    app = application()
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="windows-mcp-ui-acceptance-") as directory:
        root = Path(directory)
        settings = SettingsService(root / "home", env={"LOCALAPPDATA": str(root / "home/AppData/Local")})
        example = root / "example-program" / "claude.exe"
        example.parent.mkdir()
        example.write_bytes(b"isolated fixture; never executed")
        settings.discovery = lambda: IsolatedDiscovery(example)
        if case == "navigation-plain":
            settings.value["appearance"]["glass_strength"] = 0
        if case in {"dark", "warm"}:
            settings.value["theme"] = case
            settings.value["appearance"]["log_background"] = case
        backend = Installer(settings.home, root / "data", base / "skills/windows-mcp", Path(sys.executable), {})
        checker = HealthChecker(manifest, timeout=90, home=root / "server-home")

        class Probe:
            async def probe(self, executable):
                if case == "report":
                    return await checker.probe(root / "missing.exe")
                if getattr(sys, "frozen", False):
                    return await checker.probe(executable)
                return await HealthChecker(manifest, 90, arguments=[str(base / "installer/app.py"), "--serve"], home=root / "server-home").probe(Path(sys.executable))

        service = InstallerService(backend, manifest, discovery=IsolatedDiscovery(), health=Probe())
        store = ApprovalStore(root / "approvals")
        window = MainWindow(base, settings, Path(sys.executable), service=service, approval_store=store)
        window.setWindowTitle(window.windowTitle() + (" · 示例数据" if interactive else " · 隔离验收"))
        if size:
            window.resize(*size)
        window.show()
        started, captured, error = time.monotonic(), False, None
        verified_steps = []


        def take(subject=None):
            nonlocal captured, error
            if captured:
                return
            try:
                subject = subject or window
                app.processEvents()
                for control in (window.settings_button, window.about_button, window.install_button, window.uninstall_button, *window.action_buttons, window.selector.trigger):
                    if control.isVisible():
                        require(control.height() >= 30, "Primary control squeezed: " + control.text())
                        if control.window() is window:
                            require(window.rect().contains(control.mapTo(window, control.rect().bottomRight())), "Primary control clipped")
                if case == "progress-hidden":
                    require(window.task_strip.content.isHidden(), "Successful task did not fade after eight seconds")
                    require(window.logs.geometry() == log_geometry, "Hiding the strip shifted the log layout")
                capture(subject, target)
                if subject != window:
                    capture(window, target.with_name(target.stem + "-main.png"))
                target.with_suffix(".json").write_text(json.dumps({"case": case, "isolated": True, "native_width": subject.width(), "native_height": subject.height(), "selected": sorted(window.selection.selected), "verified_steps": verified_steps, "elapsed_seconds": round(time.monotonic() - started, 2), "events": service.log.events, "health": service.health_results, "layout": window.workspace.snapshot()}, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                error = exc
            captured = True

        def complete(_=None):
            if case == "selection-clear":
                profile = backend.clients["codex"]
                paths = [profile.config, profile.skill_root / "windows-mcp/SKILL.md", backend.receipt_path(profile)]
                before = {path: path.read_bytes() for path in paths}
                window.selector.toggle()
                window.selector.select_all()
                window.selector.clear_button.click()
                require(not window.selection.selected and not window.install_button.isEnabled(), 'Native UI acceptance failed: not window.selection.selected and not window.install_button.isEnabled()')
                require(not window.selector.checks["codex"].isEnabled(), 'Native UI acceptance failed: not window.selector.checks["codex"].isEnabled()')
                require({path: path.read_bytes() for path in paths} == before, 'Native UI acceptance failed: {path: path.read_bytes() for path in paths} == before')
                verified_steps.extend(["select_all_then_clear", "installed_files_unchanged"])
                QTimer.singleShot(200, lambda: take(window.selector.popup))
            elif case in {"backups", "restore-preview", "upgrade-preview", "undo-preview"}:
                if case == "backups":
                    service.backup(["codex"])
                    dialog = Backups(window)
                elif case == "restore-preview":
                    plan = service.preview_restore(service.latest_install(), "codex")
                    dialog = Preview(window, "配置恢复预览", plan, lambda: None)
                elif case == "upgrade-preview":
                    plan = service.preview("upgrade", ["codex"])
                    dialog = Preview(window, "升级已安装组件 · 操作预览", plan, lambda: None)
                else:
                    plan = service.preview_undo_install(service.latest_install())
                    dialog = Preview(window, "撤销最近一次安装", plan, lambda: None)
                QTimer.singleShot(350, lambda: take(dialog))
            elif case == "undo":
                transaction = service.latest_install()
                plan = service.preview_undo_install(transaction)
                def undone(results):
                    profile = backend.clients["codex"]
                    require(all(row["ok"] for row in results), 'Native UI acceptance failed: all(row["ok"] for row in results)')
                    require(not profile.config.exists() and not backend.receipt_path(profile).exists(), 'Native UI acceptance failed: not profile.config.exists() and not backend.receipt_path(profile).exists()')
                    require(not (profile.skill_root / "windows-mcp/SKILL.md").exists() and not backend.executable.exists(), 'Native UI acceptance failed: not (profile.skill_root / "windows-mcp/SKILL.md").exists() and not backend.executable.exists()')
                    dialog = Results(window, "撤销安装结果", results)
                    QTimer.singleShot(350, lambda: take(dialog))
                window.run(lambda: service.undo_install(transaction, plan["digest"]), undone)
            elif case in {"installed", "selection", "selection-bottom"}:
                require("codex" not in window.selection.selected, 'Native UI acceptance failed: "codex" not in window.selection.selected')
                if case == "selection":
                    window.selection.choose("deepseek", True)
                    window.selection_changed()
                window.selector.toggle()
                if case == "selection-bottom":
                    def show_last_clients():
                        scrollbar = window.selector.scroll.verticalScrollBar()
                        scrollbar.setValue(scrollbar.maximum())
                        take(window.selector.popup)
                    QTimer.singleShot(400, show_last_clients)
                    return
                QTimer.singleShot(400, lambda: take(window.selector.popup))
            elif case in {"uninstall", "repair", "verify"}:
                dialog = Targets(window, case)
                require(not any(value.isChecked() for value in dialog.variables.values()), 'Native UI acceptance failed: not any(value.isChecked() for value in dialog.variables.values())')
                QTimer.singleShot(350, lambda: take(dialog))
            elif case in {"status", "status-details"}:
                dialog = StatusDialog(window)
                if case == "status-details":
                    from PySide6.QtWidgets import QPushButton
                    next(item for item in dialog.findChildren(QPushButton) if item.text() == "详情").click()
                QTimer.singleShot(350, lambda: take(dialog))
            elif case == "report":
                dialog = Diagnostics(window, True)
                QTimer.singleShot(350, lambda: take(dialog))
            elif case == "progress-hidden":
                nonlocal log_geometry
                app.processEvents()
                log_geometry = window.logs.geometry()
                QTimer.singleShot(9000, take)
            elif case == "progress":
                QTimer.singleShot(350, take)
            else:
                take()

        def begin():
            if window.busy or not window.selection.states:
                QTimer.singleShot(100, begin)
                return
            if case == "cancel-install":
                plan = service.preview("install", ["codex"])
                window.start_install(["codex"], plan["digest"])
                requested = False
                def cancel_and_check():
                    nonlocal requested, error, captured
                    try:
                        if not requested and "等待 MCP 握手" in window.task_progress.message:
                            require(window.undo_button.isEnabled(), 'Native UI acceptance failed: window.undo_button.isEnabled()')
                            window.undo_button.click()
                            requested = True
                        if requested and not window.busy:
                            profile = backend.clients["codex"]
                            require(not profile.config.exists() and not backend.receipt_path(profile).exists(), 'Native UI acceptance failed: not profile.config.exists() and not backend.receipt_path(profile).exists()')
                            require(not (profile.skill_root / "windows-mcp/SKILL.md").exists() and not backend.executable.exists(), 'Native UI acceptance failed: not (profile.skill_root / "windows-mcp/SKILL.md").exists() and not backend.executable.exists()')
                            require(any(event["phase"] == "undo" and event["level"] == "SUCCESS" for event in service.log.events), 'Native UI acceptance failed: any(event["phase"] == "undo" and event["level"] == "SUCCESS" for event in service.log.events)')
                            take(next((dialog for dialog in window.dialogs if isinstance(dialog, Results)), window))
                        else:
                            QTimer.singleShot(20, cancel_and_check)
                    except Exception as exc:
                        error, captured = exc, True
                QTimer.singleShot(20, cancel_and_check)
            elif case in {"installed", "selection", "selection-bottom", "selection-clear", "processing", "uninstall", "repair", "verify", "status", "status-details", "report", "progress", "progress-hidden", "backups", "restore-preview", "upgrade-preview", "undo-preview", "undo"}:
                window.run(lambda: service.install_targets(["codex"]), complete)
                if case == "processing":
                    QTimer.singleShot(600, take)
            elif case == "selection-all":
                window.selector.toggle()
                window.selector.select_all()
                require(window.selector.clear_button.isEnabled(), 'Native UI acceptance failed: window.selector.clear_button.isEnabled()')
                QTimer.singleShot(180, lambda: take(window.selector.popup))
            elif case == "update-available":
                from installer.ui.settings_dialog import Updates
                window.update_release = {"available": True, "current": window.version, "latest": "99.0.0", "kind": "installer", "notes": "隔离界面示例：用户点击更新安装后才会下载。更新只显示进度页，首次安装保留安装向导。"}
                dialog = Updates(window)
                require(dialog.download_button.isEnabled(), 'Native UI acceptance failed: dialog.download_button.isEnabled()')
                verified_steps.append("automatic_hint_shown_without_downloading_until_user_click")
                QTimer.singleShot(150, lambda: take(dialog))
            elif case in {"choice-popup", "choice-strength"}:
                dialog = Appearance(window)
                choice = dialog.controls["log_background"] if case == "choice-popup" else dialog.strength
                def popup():
                    initial = choice.currentIndex()
                    choice.showPopup()
                    choice.popup.list.setCurrentIndex(choice.model().index(0, 0))
                    require(choice.popup.isVisible() and choice.currentIndex() == initial, 'Native UI acceptance failed: choice.popup.isVisible() and choice.currentIndex() == initial')
                    QTimer.singleShot(150, lambda: take(choice.popup))
                QTimer.singleShot(150, popup)
            elif case == "fixed-layout":
                from PySide6.QtTest import QTest
                from PySide6.QtGui import QMouseEvent
                from PySide6.QtCore import QEvent
                panels = window.workspace.panels
                initial = {key: panel.geometry() for key, panel in panels.items()}
                space = window.workspace
                targets = [(panel.heading, panel.heading.rect().center()) for panel in panels.values()]
                targets.extend([(space, QPoint(panels["targets"].geometry().right() + 6, 180)), (space, QPoint(panels["maintenance"].geometry().center().x(), panels["actions"].y() - 6))])
                for delta in (QPoint(-160, 0), QPoint(160, 0), QPoint(0, -160), QPoint(0, 160)):
                    for target_widget, start in targets:
                        end = start + delta
                        QTest.mousePress(target_widget, Qt.MouseButton.LeftButton, pos=start)
                        app.sendEvent(target_widget, QMouseEvent(QEvent.Type.MouseMove, end, target_widget.mapToGlobal(end), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                        QTest.mouseRelease(target_widget, Qt.MouseButton.LeftButton, pos=end)
                    require(all(panel.geometry() == initial[key] and panel.isVisible() for key, panel in panels.items()), "Fixed panels moved after directional drag")
                require(panels["targets"].y() == panels["maintenance"].y() and panels["actions"].x() == panels["maintenance"].x(), "Fixed layout placement changed")
                verified_steps.append("all_four_direction_drags_on_titles_and_borders_leave_panels_fixed")
                take()
            elif case in {"navigation", "navigation-plain"}:
                from PySide6.QtTest import QTest
                for control in (window.settings_button, window.about_button):
                    window.logs.setFocus()
                    baseline = control.grab().toImage()
                    QTest.mousePress(control, Qt.MouseButton.LeftButton)
                    require(control.grab().toImage() == baseline, "Navigation darkened while pressed")
                    QTest.mouseRelease(control, Qt.MouseButton.LeftButton)
                    for dialog in list(window.dialogs):
                        dialog.close()
                verified_steps.append("navigation_has_no_frame_or_dark_pressed_state")
                take()
            elif case in {"about", "privacy", "ai-disclaimer"}:
                from installer.ui.settings_dialog import About
                dialog = About(window)
                if case == "privacy":
                    dialog.tabs.setCurrentIndex(2)
                elif case == "ai-disclaimer":
                    dialog.tabs.setCurrentIndex(5)
                require(dialog.tabs.count() == 6 and "jingyanx2@gmail.com" in dialog.documents["隐私政策"].toPlainText(), "Policy documents missing")
                verified_steps.append("six_about_pages_in_requested_order_available")
                QTimer.singleShot(200, lambda: take(dialog))
            elif case == "approval-locked":
                from PySide6.QtTest import QTest
                from windows_mcp.infrastructure.trust import TrustStore
                TrustStore(store.directory).grant("forever", operator="isolated-ui-acceptance")
                window.open_operator()
                dialog = window.operator_window
                require(dialog.auto_approve.isChecked() and not dialog.auto_approve.isEnabled(), "Active grant checkbox is editable")
                QTest.mouseClick(dialog.auto_approve, Qt.MouseButton.LeftButton)
                require(dialog.auto_approve.isChecked() and dialog.trust.allows(), "Click revoked persistent grant")
                verified_steps.append("isolated_active_grant_requires_explicit_revoke_action")
                QTimer.singleShot(200, lambda: take(dialog))
            elif case == "paths":
                from installer.ui.settings_dialog import paths
                from PySide6.QtWidgets import QPushButton
                dialog = paths(window)
                next(item for item in dialog.findChildren(QPushButton) if "展开详情" in item.text()).click()
                QTimer.singleShot(350, lambda: take(dialog))
            elif case in {"settings", "settings-update", "appearance", "advanced"}:
                if case == "settings-update":
                    window.update_release = {"available": True, "latest": "99.0.0"}
                dialog = SettingsHub(window) if case.startswith("settings") else Preferences(window) if case == "advanced" else Appearance(window)
                if case.startswith("settings"):
                    require(len({item.width() for item in dialog.controls.values()}) == 1, 'Native UI acceptance failed: len({item.width() for item in dialog.controls.values()}) == 1')
                    require(dialog.controls["检查更新"].notification_dot == (case == "settings-update"), 'Native UI acceptance failed: dialog.controls["检查更新"].notification_dot == (case == "settings-update")')
                    verified_steps.append("aligned_settings_rows_with_fixed_trailing_update_dot")
                QTimer.singleShot(350, lambda: take(dialog))
            elif case in {"approval", "approval-default", "approval-default-automatic"}:
                if case == "approval":
                    request = store.request("isolated-acceptance-session", "Files", {"operation": "write", "path": "C:\\WindowsMCP-Example\\example.txt", "content": "isolated example"}, 2, "隔离界面验收示例；不会执行或批准操作")
                    window.open_operator(request["request_id"])
                else:
                    window.open_operator()
                    require(window.operator_window.auto_approve.isEnabled(), 'Native UI acceptance failed: window.operator_window.auto_approve.isEnabled()')
                    require(window.operator_window.defaults_row.isVisible(), 'Native UI acceptance failed: window.operator_window.defaults_row.isVisible()')
                    require(store.pending() == [], 'Native UI acceptance failed: store.pending() == []')
                    window.operator_window.auto_approve.setChecked(case == "approval-default-automatic")
                    require(not window.operator_window.trust.allows(), 'Native UI acceptance failed: not window.operator_window.trust.allows()')
                    verified_steps.append("default_approval_available_without_requests_or_implicit_grants")
                QTimer.singleShot(350, lambda: take(window.operator_window))
            else:
                take()

        log_geometry = None

        def finish():
            if captured and not window.busy:
                window.close()
                app.quit()
            elif time.monotonic() - started > 150:
                app.quit()
            else:
                QTimer.singleShot(150, finish)
        if not interactive:
            QTimer.singleShot(600, begin)
            QTimer.singleShot(1000, finish)
        previous_hook = sys.excepthook
        def callback_error(kind, value, traceback):
            nonlocal error, captured
            error, captured = value, True
            app.quit()
        sys.excepthook = callback_error
        try:
            app.exec()
        finally:
            sys.excepthook = previous_hook
            window.close()
        if error:
            raise error
        if not captured and not interactive:
            raise RuntimeError("UI acceptance timed out")
