"""One executable: setup window by default, packaged MCP server with --serve."""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.backend import Configuration, Installer, mcp_entry


def resources() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def restore_stdio() -> None:
    """The windowed Windows bootloader sets Python streams to None; MCP inherits pipes."""
    if os.name != "nt":
        return
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    process = kernel.GetCurrentProcess()
    for name, number, mode, flag in [("stdin", -10, "rb", os.O_RDONLY), ("stdout", -11, "wb", os.O_WRONLY), ("stderr", -12, "wb", os.O_WRONLY)]:
        if getattr(sys, name) is not None:
            continue
        handle = kernel.GetStdHandle(number)
        if not handle or handle == ctypes.c_void_p(-1).value:
            kernel.AttachConsole(wintypes.DWORD(-1))
            handle = kernel.GetStdHandle(number)
        if not handle or handle == ctypes.c_void_p(-1).value:
            stream = open(os.devnull, "r" if mode == "rb" else "w", encoding="utf-8")
        else:
            duplicate = wintypes.HANDLE()
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            fd = msvcrt.open_osfhandle(duplicate.value, flag | os.O_BINARY)
            stream = io.TextIOWrapper(os.fdopen(fd, mode, buffering=0), encoding="utf-8", newline="\n", write_through=True)
        setattr(sys, name, stream)


async def check_service(command: Path, *, home: Path | None = None) -> dict:
    from installer.health import HealthChecker
    manifest = json.loads((resources() / "manifest.json").read_text(encoding="utf-8"))
    result = await HealthChecker(manifest, 90, home=home).probe(command)
    if not result.ok:
        raise RuntimeError(f"{result.code}: {result.message}")
    return result.to_dict()


def isolated_test(root: Path) -> None:
    if root.exists() and any(root.iterdir()):
        raise ValueError("自测只能使用空的临时目录。")
    root.mkdir(parents=True, exist_ok=True)
    backend = Installer(root / "home", root / "app", resources() / "skills" / "windows-mcp", Path(sys.executable), {})
    from installer.operations import InstallerService
    from installer.models import Presence, ManagerError
    class Discovery:
        def detect(self, profile):
            return Presence.DETECTED, "isolated self-test"
    manifest = json.loads((resources() / "manifest.json").read_text(encoding="utf-8"))
    from installer.health import HealthChecker
    service = InstallerService(backend, manifest, discovery=Discovery(), health=HealthChecker(manifest, 90, home=root / "server-home"))
    results = service.install_targets(list(backend.clients))
    assert all(row["ok"] for row in results), results
    for profile in backend.clients.values():
        assert (profile.skill_root / "windows-mcp" / "SKILL.md").is_file()
        assert Configuration(profile, profile.config.read_bytes()).get() == mcp_entry(profile, backend.executable)
    installed = list(backend.clients)
    try:
        service.install_targets(["codex"])
    except ManagerError as exc:
        assert exc.code == "INSTALL_REJECTED"
    else:
        raise AssertionError("Duplicate installation was accepted")
    for profile in backend.clients.values():
        plan = service.preview("uninstall", [profile.id])
        result = service.apply_plan("uninstall", [profile.id], plan["digest"])
        assert result[0]["ok"], result
        assert Configuration(profile, profile.config.read_bytes()).get() is None
    assert not backend.executable.exists()
    (root / "result.json").write_text(json.dumps({"version": manifest["version"], "profiles": installed, "install_and_uninstall": "PASS", "isolated": True, "duplicate_rejected": True, "real_handshakes": len(results), "zero_reference_runtime_cleanup": True}, indent=2), encoding="utf-8")


def gui(review_request=None, finish_plan=None, update_nonce=None):
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))
    from installer.backend import atomic_write
    from installer.settings import SettingsService
    from installer.storage import ProcessLock, private_directory
    from installer.models import ManagerError
    from windows_mcp.infrastructure.approvals import ApprovalStore
    from installer.ui.main_window import MainWindow
    from installer.ui.theme import application
    from PySide6.QtCore import QTimer

    settings = SettingsService(Path.home())
    private_directory(settings.path.parent)
    wake = settings.path.parent / "manager-wake.json"
    try:
        with ProcessLock(settings.path.parent / "manager-ui.lock"):
            app = application()
            window = MainWindow(resources(), settings, Path(sys.executable), approval_store=ApprovalStore(settings.path.parent / "approvals"), wake_path=wake, auto_scan=finish_plan is None)
            window.show()
            if review_request:
                QTimer.singleShot(400, lambda: window.open_operator(review_request))
            if finish_plan:
                from installer.updates import load_plan
                row = load_plan(finish_plan, update_nonce)
                if Path(row["target"]).resolve() != Path(sys.executable).resolve() or row["version"] != window.version:
                    raise ManagerError("UPDATE_PLAN_INVALID", "新版程序与更新计划不一致")

                def finish():
                    try:
                        ids = window.service.upgrade_targets()
                        if ids:
                            plan = window.service.preview("upgrade", ids)
                            window.service.apply_plan("upgrade", ids, plan["digest"])
                        atomic_write(finish_plan.parent / "result.json", b'{"ok":true}')
                        return True
                    except Exception:
                        atomic_write(finish_plan.parent / "result.json", b'{"ok":false}')
                        return False
                QTimer.singleShot(500, lambda: window.run(finish, lambda ok: None if ok else window.close()))
            app.exec()
    except ManagerError as exc:
        if exc.code != "INSTALLER_BUSY":
            raise
        atomic_write(wake, json.dumps({"request_id": review_request}).encode())
        if finish_plan:
            atomic_write(finish_plan.parent / "result.json", b'{"ok":false}')


def main():
    # Frozen windowed 'approve ID' now opens the local human review UI. It never
    # tries to read a hidden terminal confirmation or grants permission itself.
    if len(sys.argv) > 2 and sys.argv[1] == "approve" and getattr(sys, "frozen", False):
        gui(review_request=sys.argv[2])
        return
    if len(sys.argv) > 1 and (sys.argv[1] == "--serve" or sys.argv[1] in {"serve", "approve", "auth"}):
        restore_stdio()
        from windows_mcp.__main__ import main as server_main
        arguments = ["serve", *sys.argv[2:]] if sys.argv[1] == "--serve" else sys.argv[1:]
        server_main(args=arguments)
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--portable-check", type=Path)
    parser.add_argument("--review-request")
    parser.add_argument("--apply-update", type=Path)
    parser.add_argument("--finish-update", type=Path)
    parser.add_argument("--update-nonce")
    parser.add_argument("--ui-smoke", type=Path)
    parser.add_argument("--ui-smoke-select", nargs="*", default=[])
    parser.add_argument("--ui-smoke-size", nargs=2, type=int)
    parser.add_argument("--ui-smoke-case", choices=["ready", "installed", "processing", "uninstall", "settings", "report", "approval", "status", "verify", "repair", "appearance", "docking", "dark", "warm", "advanced", "progress", "progress-hidden", "edge-preview", "edge-parked"], default="ready")
    parser.add_argument("--ui-smoke-scale", type=float, default=1.0)
    args = parser.parse_args()
    if args.self_test:
        isolated_test(args.self_test)
    elif args.portable_check:
        from installer.portable_check import check
        check(args.portable_check)
    elif args.apply_update:
        from installer.updates import apply_update
        apply_update(args.apply_update, args.update_nonce)
    elif args.ui_smoke:
        from installer.ui.smoke import run
        run(resources(), args.ui_smoke, args.ui_smoke_case, args.ui_smoke_size, args.ui_smoke_scale)
    else:
        gui(args.review_request, args.finish_update, args.update_nonce)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if getattr(sys, "frozen", False):
            # A windowed PyInstaller bootloader shows an error dialog for an
            # unhandled exception. Acceptance commands must fail and report,
            # so a headless runner never hangs waiting for that dialog.
            for flag in ("--portable-check", "--ui-smoke", "--self-test"):
                if flag in sys.argv:
                    destination = Path(sys.argv[sys.argv.index(flag) + 1])
                    if flag == "--self-test":
                        destination = destination / "acceptance-error.json"
                    else:
                        destination = destination.with_suffix(".error.json")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=True), encoding="utf-8")
                    raise SystemExit(1) from exc
        if getattr(sys, "frozen", False) and not any(arg in sys.argv for arg in ("--serve", "serve", "auth", "--self-test", "--portable-check", "--apply-update", "--ui-smoke")):
            ctypes.windll.user32.MessageBoxW(None, f"管理器未能启动：{exc}\n原配置与文件已保留。", "Windows MCP", 16)
        raise
