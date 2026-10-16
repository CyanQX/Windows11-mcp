"""Native visual acceptance in temporary homes, including actual frozen stdio probes."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QPoint, QEvent, Qt
from PySide6.QtGui import QMouseEvent

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
    def detect(self, profile):
        return Presence.DETECTED, "isolated UI acceptance client"


def run(base, target, case="ready", size=None, scale=1.0, *, interactive=False):
    if not 0.75 <= scale <= 2.0:
        raise ValueError("UI acceptance scale outside bounds")
    if scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(scale)
    app = application()
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="windows-mcp-ui-acceptance-") as directory:
        root = Path(directory)
        settings = SettingsService(root / "home", env={"LOCALAPPDATA": str(root / "home/AppData/Local")})
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

        def drag_boundary(start, delta):
            canvas = window.workspace.canvas
            app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseButtonPress, start, canvas.mapToGlobal(start), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            count = max(2, (abs(delta.x()) + abs(delta.y())) // 8)
            for step in range(1, count + 1):
                point = start + QPoint(round(delta.x() * step / count), round(delta.y() * step / count))
                app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, point, canvas.mapToGlobal(point), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                app.processEvents()
            point = start + delta
            app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, point, canvas.mapToGlobal(point), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        def take(subject=None):
            nonlocal captured, error
            if captured:
                return
            try:
                subject = subject or window
                app.processEvents()
                for control in (window.install_button, window.uninstall_button, *window.action_buttons, window.selector.trigger):
                    if control.isVisible():
                        assert control.height() >= 30, "Primary control squeezed: " + control.text()
                        if control.window() is window:
                            assert window.rect().contains(control.mapTo(window, control.rect().bottomRight())), "Primary control clipped"
                if case == "progress-hidden":
                    assert window.task_strip.content.isHidden(), "Successful task did not fade after eight seconds"
                    assert window.logs.geometry() == log_geometry, "Hiding the strip shifted the log layout"
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
                assert not window.selection.selected and not window.install_button.isEnabled()
                assert not window.selector.checks["codex"].isEnabled()
                assert {path: path.read_bytes() for path in paths} == before
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
                    assert all(row["ok"] for row in results)
                    assert not profile.config.exists() and not backend.receipt_path(profile).exists()
                    assert not (profile.skill_root / "windows-mcp/SKILL.md").exists() and not backend.executable.exists()
                    dialog = Results(window, "撤销安装结果", results)
                    QTimer.singleShot(350, lambda: take(dialog))
                window.run(lambda: service.undo_install(transaction, plan["digest"]), undone)
            elif case in {"installed", "selection"}:
                assert "codex" not in window.selection.selected
                if case == "selection":
                    window.selection.choose("deepseek", True)
                    window.selection_changed()
                window.selector.toggle()
                QTimer.singleShot(400, lambda: take(window.selector.popup))
            elif case in {"uninstall", "repair", "verify"}:
                dialog = Targets(window, case)
                assert not any(value.isChecked() for value in dialog.variables.values())
                QTimer.singleShot(350, lambda: take(dialog))
            elif case == "status":
                dialog = StatusDialog(window)
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
                            assert window.undo_button.isEnabled()
                            window.undo_button.click()
                            requested = True
                        if requested and not window.busy:
                            profile = backend.clients["codex"]
                            assert not profile.config.exists() and not backend.receipt_path(profile).exists()
                            assert not (profile.skill_root / "windows-mcp/SKILL.md").exists() and not backend.executable.exists()
                            assert any(event["phase"] == "undo" and event["level"] == "SUCCESS" for event in service.log.events)
                            take(next((dialog for dialog in window.dialogs if isinstance(dialog, Results)), window))
                        else:
                            QTimer.singleShot(20, cancel_and_check)
                    except Exception as exc:
                        error, captured = exc, True
                QTimer.singleShot(20, cancel_and_check)
            elif case in {"installed", "selection", "selection-clear", "processing", "uninstall", "repair", "verify", "status", "report", "progress", "progress-hidden", "backups", "restore-preview", "upgrade-preview", "undo-preview", "undo"}:
                window.run(lambda: service.install_targets(["codex"]), complete)
                if case == "processing":
                    QTimer.singleShot(600, take)
            elif case == "selection-all":
                window.selector.toggle()
                window.selector.select_all()
                assert window.selector.clear_button.isEnabled()
                QTimer.singleShot(180, lambda: take(window.selector.popup))
            elif case.startswith("targets-nested-"):
                space = window.workspace
                dock, actions = space.docks["targets"], space.docks["actions"]
                space.canvas.splitDockWidget(dock, actions, Qt.Orientation.Vertical)
                def shrink_target():
                    initial = actions.height()
                    start = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
                    minimum = space.divider_minimum(dock, "left").width()
                    destination = QPoint(minimum - round(minimum * .65), start.y())
                    canvas = space.canvas
                    app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseButtonPress, start, canvas.mapToGlobal(start), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                    for x in range(start.x(), destination.x() - 1, -8):
                        point = QPoint(x, start.y())
                        app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, point, canvas.mapToGlobal(point), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                        app.processEvents()
                    assert space.preview and space.preview.dock is dock and set(space.preview.members) == {dock, actions}
                    assert actions.isVisible() and abs(actions.height() - initial) <= 16, f"Toolbar changed {initial} -> {actions.height()}; target {dock.geometry()}; shared {space.preview.shared_span}"
                    verified_steps.append("whole_left_side_slides_continuously_with_both_panels")
                    if case == "targets-nested-preview":
                        take()
                        return
                    app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, destination, canvas.mapToGlobal(destination), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                    QTimer.singleShot(310, target_parked)
                def target_parked():
                    assert dock.isHidden() and space.collapsed["targets"][0] == "left"
                    assert actions.isHidden() and len(space.collapsed) == 1
                    assert set(space.park_groups["targets"]) == {"targets", "actions"}
                    verified_steps.append("whole_left_side_parks_with_one_centered_handle")
                    if case == "targets-nested-restored":
                        space.collapsed["targets"][1].control.click()
                        QTimer.singleShot(350, target_restored)
                    else:
                        take()
                def target_restored():
                    assert dock.isVisible() and dock.x() >= 0 and "targets" not in space.collapsed
                    assert actions.height() <= 180
                    assert actions.isVisible()
                    verified_steps.append("side_handle_restores_all_panels_and_compact_actions")
                    take()
                QTimer.singleShot(80, shrink_target)
            elif case == "update-available":
                from installer.ui.settings_dialog import Updates
                window.update_release = {"available": True, "current": window.version, "latest": "1.4.2", "kind": "installer", "notes": "隔离界面示例：用户点击更新安装后才会下载。更新只显示进度页，首次安装保留安装向导。"}
                dialog = Updates(window)
                assert dialog.download_button.isEnabled()
                verified_steps.append("automatic_hint_shown_without_downloading_until_user_click")
                QTimer.singleShot(150, lambda: take(dialog))
            elif case.startswith("footer-"):
                original = window.size()
                space, dock = window.workspace, window.workspace.docks["actions"]
                space.canvas.splitDockWidget(space.docks["targets"], dock, Qt.Orientation.Vertical)
                def maximize():
                    assert space.last_area["actions"] == "left"
                    window.showMaximized()
                    QTimer.singleShot(120, enlarge)
                def enlarge():
                    start = QPoint(dock.geometry().center().x(), dock.y() - 5)
                    assert space.divider_snapshot(space.canvas.mapToGlobal(start))["actions"][1] == "bottom"
                    drag_boundary(start, QPoint(0, -230))
                    verified_steps.append("nested_vertical_resize_while_maximized")
                    if case.startswith("footer-floating"):
                        title = dock.titleBarWidget()
                        point = space.canvas.mapToGlobal(QPoint(-180, 180))
                        space.start_title_drag(dock, title.mapToGlobal(title.rect().center()))
                        space.drag_title(point)
                        space.finish_title_drag(point)
                        assert dock.isFloating()
                        dock.resize(580, 400)
                    if case == "footer-minimized":
                        window.showMinimized()
                        QTimer.singleShot(80, restore)
                    else:
                        restore()
                def restore():
                    window.showNormal()
                    window.resize(original)
                    QTimer.singleShot(150, restored)
                def restored():
                    assert dock.height() <= 180
                    verified_steps.append("compact_after_window_restore")
                    if case.startswith("footer-floating"):
                        assert dock.isFloating()
                        if case == "footer-floating":
                            take(dock)
                            return
                        start = QPoint(dock.width() // 2, 1)
                        point = dock.mapToGlobal(start)
                        app.sendEvent(dock, QMouseEvent(QEvent.Type.MouseButtonPress, start, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                        assert space.floating_resize
                        for distance in range(0, 250, 8):
                            dest = point + QPoint(0, distance)
                            app.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseMove, space.canvas.mapFromGlobal(dest), dest, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                            app.processEvents()
                        app.sendEvent(space.canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, space.canvas.mapFromGlobal(dest), dest, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                    elif case in {"footer-nested-parked", "footer-minimized"}:
                        drag_boundary(QPoint(dock.geometry().center().x(), dock.y() - 5), QPoint(0, dock.height() + 100))
                    else:
                        take()
                        return
                    QTimer.singleShot(310, parked)
                def parked():
                    assert dock.isHidden() and space.collapsed["actions"][0] == "bottom"
                    verified_steps.append("shrink_below_minimum_parks_at_bottom")
                    if case == "footer-minimized":
                        space.collapsed["actions"][1].control.click()
                        QTimer.singleShot(230, reopened)
                    else:
                        take()
                def reopened():
                    assert dock.isVisible() and dock.height() <= 180
                    verified_steps.append("edge_handle_restores_compact_toolbar")
                    take()
                QTimer.singleShot(80, maximize)
            elif case in {"choice-popup", "choice-strength"}:
                dialog = Appearance(window)
                choice = dialog.controls["log_background"] if case == "choice-popup" else dialog.strength
                def popup():
                    initial = choice.currentIndex()
                    choice.showPopup()
                    choice.popup.list.setCurrentIndex(choice.model().index(0, 0))
                    assert choice.popup.isVisible() and choice.currentIndex() == initial
                    QTimer.singleShot(150, lambda: take(choice.popup))
                QTimer.singleShot(150, popup)
            elif case == "drop-preview":
                space, dock = window.workspace, window.workspace.docks["targets"]
                title = dock.titleBarWidget()
                space.start_title_drag(dock, title.mapToGlobal(title.rect().center()))
                space.drag_title(space.canvas.mapToGlobal(QPoint(space.canvas.width() - 20, 220)))
                assert dock.isFloating() and space.drop_preview.isVisible()
                QTimer.singleShot(200, take)
            elif case == "resize-redock":
                original = window.size()
                space, dock = window.workspace, window.workspace.docks["actions"]
                window.showMaximized()
                def float_and_restore():
                    dock.setFloating(True)
                    dock.resize(1100, 600)
                    window.showNormal()
                    window.resize(original)
                    space.move_to(dock, "bottom")
                    def checked():
                        assert dock.height() <= 180, "Stale maximized footer extent"
                        take()
                    QTimer.singleShot(250, checked)
                QTimer.singleShot(200, float_and_restore)
            elif case in {"settings", "appearance", "advanced"}:
                dialog = SettingsHub(window) if case == "settings" else Preferences(window) if case == "advanced" else Appearance(window)
                QTimer.singleShot(350, lambda: take(dialog))
            elif case == "approval":
                request = store.request("isolated-acceptance-session", "Files", {"operation": "write", "path": "C:\\WindowsMCP-Example\\example.txt", "content": "isolated example"}, 2, "隔离界面验收示例；不会执行或批准操作")
                window.open_operator(request["request_id"])
                QTimer.singleShot(350, lambda: take(window.operator_window))
            elif case == "docking":
                window.workspace.docks["maintenance"].setFloating(True)
                window.workspace.collapse(window.workspace.docks["targets"])
                QTimer.singleShot(350, take)
            elif case in {"edge-preview", "edge-parked", "edge-right", "edge-bottom", "edge-top"}:
                space, canvas = window.workspace, window.workspace.canvas
                if case in {"edge-right", "edge-bottom", "edge-top"}:
                    from installer.ui.workspace import SlidePreview
                    edge = case[5:]
                    dock = space.docks["maintenance" if edge == "right" else "actions"]
                    space.move_to(dock, edge)
                    canvas.layout().activate()
                    original = dock.size()
                    space.preview = SlidePreview(space, dock, edge)
                    space.preview.advance(.55)
                    actual = dock.width() if edge == "right" else dock.height()
                    assert actual < (original.width() if edge == "right" else original.height()) * .65
                    QTimer.singleShot(200, take)
                    return
                dock = space.docks["targets"]
                point = QPoint(dock.geometry().right() + 5, dock.geometry().center().y())
                app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseButtonPress, point, canvas.mapToGlobal(point), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                minimum = dock.minimumSizeHint().width()
                destination = QPoint(minimum - round(minimum * (.55 if case == "edge-preview" else .7)), point.y())
                for x in range(point.x(), destination.x() - 1, -8):
                    position = QPoint(x, point.y())
                    app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, position, canvas.mapToGlobal(position), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                    app.processEvents()
                assert space.preview and dock.isVisible() and "targets" not in space.collapsed
                if case == "edge-parked":
                    app.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseButtonRelease, destination, canvas.mapToGlobal(destination), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                QTimer.singleShot(350, take)
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
            window.workspace.cancel_divider_gesture()
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
