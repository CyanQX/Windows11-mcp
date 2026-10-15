"""Native visual acceptance in temporary homes, including actual frozen stdio probes."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer

from installer.backend import Installer
from installer.health import HealthChecker
from installer.models import Presence
from installer.operations import InstallerService
from installer.settings import SettingsService
from installer.ui.capture import capture
from installer.ui.dialogs import Diagnostics, StatusDialog, Targets
from installer.ui.main_window import MainWindow
from installer.ui.settings_dialog import Appearance, SettingsHub
from installer.ui.theme import application
from windows_mcp.infrastructure.approvals import ApprovalStore


class IsolatedDiscovery:
    def detect(self, profile):
        return Presence.DETECTED, "isolated UI acceptance client"


def run(base, target, case="ready", size=None, scale=1.0):
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
        window.setWindowTitle(window.windowTitle() + " · 隔离验收")
        if size:
            window.resize(*size)
        window.show()
        started, captured, error = time.monotonic(), False, None

        def take(subject=None):
            nonlocal captured, error
            if captured:
                return
            try:
                subject = subject or window
                app.processEvents()
                if case != "docking":
                    for control in (window.install_button, window.uninstall_button, *window.action_buttons, window.selector.trigger):
                        assert control.height() >= 30, "Primary control squeezed"
                        assert window.rect().contains(control.mapTo(window, control.rect().bottomRight())), "Primary control clipped"
                capture(subject, target)
                if subject != window:
                    capture(window, target.with_name(target.stem + "-main.png"))
                target.with_suffix(".json").write_text(json.dumps({"case": case, "isolated": True, "native_width": subject.width(), "native_height": subject.height(), "selected": sorted(window.selection.selected), "elapsed_seconds": round(time.monotonic() - started, 2), "events": service.log.events, "health": service.health_results, "layout": window.workspace.snapshot()}, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                error = exc
            captured = True

        def complete(_=None):
            if case == "installed":
                assert "codex" not in window.selection.selected
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
            else:
                take()

        def begin():
            if window.busy or not window.selection.states:
                QTimer.singleShot(100, begin)
                return
            if case in {"installed", "processing", "uninstall", "repair", "verify", "status", "report"}:
                window.run(lambda: service.install_targets(["codex"]), complete)
                if case == "processing":
                    QTimer.singleShot(600, take)
            elif case in {"settings", "appearance"}:
                dialog = SettingsHub(window) if case == "settings" else Appearance(window)
                QTimer.singleShot(350, lambda: take(dialog))
            elif case == "approval":
                request = store.request("isolated-acceptance-session", "Files", {"operation": "write", "path": "C:\\WindowsMCP-Example\\example.txt", "content": "isolated example"}, 2, "隔离界面验收示例；不会执行或批准操作")
                window.open_operator(request["request_id"])
                QTimer.singleShot(350, lambda: take(window.operator_window))
            elif case == "docking":
                window.workspace.docks["maintenance"].setFloating(True)
                window.workspace.collapse(window.workspace.docks["targets"])
                QTimer.singleShot(350, take)
            else:
                take()

        def finish():
            if captured and not window.busy:
                window.close()
                app.quit()
            elif time.monotonic() - started > 150:
                app.quit()
            else:
                QTimer.singleShot(150, finish)
        QTimer.singleShot(600, begin)
        QTimer.singleShot(1000, finish)
        app.exec()
        if error:
            raise error
        if not captured:
            raise RuntimeError("UI acceptance timed out")
