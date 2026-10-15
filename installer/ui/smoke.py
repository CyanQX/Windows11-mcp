"""Opt-in native UI acceptance in temporary homes; no live client configuration."""

import json
import sys
import tempfile
import time
from pathlib import Path

import customtkinter as ctk

from installer.backend import Installer
from installer.health import HealthChecker
from installer.models import Presence
from installer.operations import InstallerService
from installer.settings import SettingsService
from installer.ui.capture import capture
from installer.ui.dialogs import Diagnostics, Targets
from installer.ui.main_window import MainWindow
from installer.ui.settings_dialog import SettingsHub
from installer.ui.theme import work_area
from windows_mcp.infrastructure.approvals import ApprovalStore


class IsolatedDiscovery:
    def detect(self, profile):
        return Presence.DETECTED, "isolated UI acceptance client"


def run(base, target, case="ready", size=None, scale=1.0):
    if not 0.75 <= scale <= 2.0:
        raise ValueError("UI acceptance scale outside bounds")
    ctk.set_widget_scaling(scale)
    ctk.set_window_scaling(scale)
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="windows-mcp-ui-acceptance-") as directory:
        root = Path(directory)
        settings = SettingsService(root / "home", env={"LOCALAPPDATA": str(root / "appdata")})
        backend = Installer(settings.home, root / "data", base / "skills" / "windows-mcp", Path(sys.executable), {})
        checker = HealthChecker(manifest, timeout=90, home=root / "server-home")

        class SourceProbe:
            async def probe(self, executable):
                if case == "report":
                    return await checker.probe(root / "missing.exe")
                if getattr(sys, "frozen", False):
                    return await checker.probe(executable)
                return await HealthChecker(manifest, 90, arguments=[str(base / "installer" / "app.py"), "--serve"], home=root / "server-home").probe(Path(sys.executable))

        service = InstallerService(backend, manifest, discovery=IsolatedDiscovery(), health=SourceProbe())
        store = ApprovalStore(root / "approvals")
        window = MainWindow(base, settings, Path(sys.executable), service=service, approval_store=store)
        window.title(window.title() + " · 隔离验收")
        if size:
            window.geometry(f"{size[0]}x{size[1]}")
        started = time.monotonic()
        captured = False
        error = None

        def take(subject=None):
            nonlocal captured, error
            if captured:
                return
            try:
                subject = subject or window
                subject.update_idletasks()
                for control in (window.install_button, window.uninstall_button, *window.action_buttons, window.selector.trigger):
                    assert control.winfo_height() > 25 * scale, "Primary control squeezed"
                    assert control.winfo_rooty() + control.winfo_height() <= window.winfo_rooty() + window.winfo_height() + 2, "Primary control clipped"
                    assert control.winfo_rootx() + control.winfo_width() <= window.winfo_rootx() + window.winfo_width() + 2, "Primary control horizontally clipped"
                capture(subject, target)
                if subject != window:
                    capture(window, target.with_name(target.stem + "-main.png"))
                target.with_suffix(".json").write_text(json.dumps({"case": case, "isolated": True, "scale": scale, "native_width": subject.winfo_width(), "native_height": subject.winfo_height(), "root_xy": [subject.winfo_rootx(), subject.winfo_rooty()], "work_area": work_area(subject), "selected": sorted(window.selection.selected), "elapsed_seconds": round(time.monotonic() - started, 2), "events": service.log.events, "health": service.health_results}, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                error = exc
            captured = True

        def complete(_=None):
            if case == "installed":
                assert "codex" not in window.selection.selected
                window.selector.toggle()
                window.after(650, lambda: take(window.selector.popup))
            elif case == "uninstall":
                dialog = Targets(window, "uninstall")
                assert not any(v.get() for v in dialog.variables.values())
                window.after(650, lambda: take(dialog))
            elif case == "report":
                dialog = Diagnostics(window, True)
                window.after(650, lambda: take(dialog))
            else:
                take()

        def begin():
            if window.busy or not window.selection.states:
                window.after(100, begin)
                return
            if case in {"installed", "processing", "uninstall", "report"}:
                window.run(lambda: service.install_targets(["codex"]), complete)
                if case == "processing":
                    window.after(600, lambda: take())
            elif case == "settings":
                dialog = SettingsHub(window)
                window.after(650, lambda: take(dialog))
            elif case == "approval":
                request = store.request("isolated-acceptance-session", "Files", {"operation": "write", "path": "C:\\WindowsMCP-Example\\example.txt", "content": "isolated example"}, 2, "仅用于隔离界面验收；不会执行操作")
                window.open_operator(request["request_id"])
                window.after(650, lambda: take(window.operator_window))
            else:
                take()

        def finish():
            if captured and not window.busy or time.monotonic() - started > 150:
                window.destroy()
            else:
                window.after(200, finish)
        window.after(600, begin)
        window.after(1000, finish)
        window.mainloop()
        if error:
            raise error
        if not captured:
            raise RuntimeError("UI acceptance timed out")


if __name__ == "__main__":
    raise SystemExit("Use installer/app.py --ui-smoke FILE")
