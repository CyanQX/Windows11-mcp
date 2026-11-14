"""Real Windows mutex lifetime and release-note visibility persistence."""
import json
import subprocess
import sys

import pytest

from installer.instance import ManagerInstance
from installer.models import ManagerError
from installer.release_notes import ReleaseNotesState


def test_real_mutex_excludes_child_and_releases_on_exit(tmp_path):
    child = "from pathlib import Path; from installer.instance import ManagerInstance; c=ManagerInstance(Path(__import__('sys').argv[1])); c.__enter__(); c.__exit__()"
    with ManagerInstance(tmp_path):
        with pytest.raises(ManagerError, match="INSTALLER_BUSY"):
            ManagerInstance(tmp_path).__enter__()
        result = subprocess.run([sys.executable, "-c", child, str(tmp_path)], capture_output=True, timeout=10)
        assert result.returncode != 0 and b"INSTALLER_BUSY" in result.stderr
    assert subprocess.run([sys.executable, "-c", child, str(tmp_path)], capture_output=True, timeout=10).returncode == 0


def test_concurrent_native_wakes_survive_locked_legacy_file_and_early_startup(tmp_path):
    import win32con
    import win32file

    wake = tmp_path / "manager-wake.json"
    wake.write_bytes(b'{"request_id":null}')
    # Reproduce Windows' no-FILE_SHARE_DELETE reader that broke os.replace.
    locked = win32file.CreateFile(str(wake), win32con.GENERIC_READ, win32con.FILE_SHARE_READ,
                                 None, win32con.OPEN_EXISTING, 0, None)
    child = """
import sys
from pathlib import Path
from installer.instance import ManagerInstance
from installer.models import ManagerError
try:
    with ManagerInstance(Path(sys.argv[1]), review=len(sys.argv) > 2):
        raise SystemExit(5)
except ManagerError as exc:
    if exc.code != 'INSTALLER_BUSY':
        raise
"""
    try:
        with ManagerInstance(tmp_path) as owner:
            assert owner.consume_activation() is None
            children = [subprocess.Popen([sys.executable, "-c", child, str(tmp_path)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
            for process in children:
                _, error = process.communicate(timeout=15)
                assert process.returncode == 0, error
            # Poll only after every child exits, just as while Qt loads.
            assert owner.consume_activation() == "activate"
            assert owner.consume_activation() is None
            assert subprocess.run([sys.executable, "-c", child, str(tmp_path), "review"],
                                  capture_output=True, timeout=10).returncode == 0
            assert owner.consume_activation() == "review"
            with ManagerInstance(tmp_path / "other-home") as other:
                assert other.consume_activation() is None
            assert wake.read_bytes() == b'{"request_id":null}'
    finally:
        locked.Close()
    with ManagerInstance(tmp_path) as restarted:
        assert restarted.consume_activation() is None


def test_release_notes_are_user_and_version_scoped_and_atomic(tmp_path):
    state = ReleaseNotesState(tmp_path / "user-A", "1.9.10")
    assert not state.shown() and not state.path.exists()
    assert state.record_visible()
    assert state.shown() and not state.record_visible()
    assert ReleaseNotesState(tmp_path / "user-A", "1.9.10").shown()
    assert not ReleaseNotesState(tmp_path / "user-B", "1.9.10").shown()
    assert not ReleaseNotesState(tmp_path / "user-A", "1.9.11").shown()
    assert list(json.loads(state.path.read_text())["versions"]) == ["1.9.10"]


def test_notes_record_only_after_visible_and_manual_open_does_not_reset(tmp_path):
    from pathlib import Path
    from installer.settings import SettingsService
    from installer.ui.theme import application
    from installer.ui.main_window import MainWindow
    from installer.release_notes import show_release_notes
    from tests.installer_helpers import make_service

    app = application()
    settings = SettingsService(tmp_path / "home", env={"LOCALAPPDATA": str(tmp_path / "home/AppData/Local")})
    owner = MainWindow(Path(__file__).resolve().parents[1], settings, Path(sys.executable), service=make_service(tmp_path / "fixture"), auto_scan=False)
    owner.show()
    state = ReleaseNotesState(settings.home, owner.version)
    assert not state.shown()
    sheet = show_release_notes(owner, automatic=True)
    for _ in range(4):
        app.processEvents()
    assert sheet.isVisible() and state.shown()
    timestamp = state.path.read_bytes()
    sheet.close()
    app.processEvents()
    assert show_release_notes(owner, automatic=True) is None
    sheet = show_release_notes(owner)
    app.processEvents()
    assert sheet.isVisible() and state.path.read_bytes() == timestamp
    sheet.close()
    owner.close()
    app.processEvents()


def test_startup_log_is_bounded_and_does_not_record_exception_secrets(tmp_path):
    from installer.startup import StartupTrace
    trace = StartupTrace(tmp_path)
    trace.record("ready")
    trace.record("failed", status="failed", error=ValueError("secret-key-should-not-appear"))
    text = trace.path.read_text()
    assert "secret-key" not in text and '"error_code": "ValueError"' in text
    trace.path.write_text("x" * (1048576 + 1))
    trace.record("next")
    assert trace.path.stat().st_size < 1000
