"""Acceptance error handling recognizes both supported option-value spellings."""
import pytest
import builtins
import sys

from installer.app import argument_value


@pytest.mark.parametrize("arguments", [
    ["--ui-smoke", "C:/test with spaces/page.png", "--ui-smoke-case", "ready"],
    ["--ui-smoke=C:/test with spaces/page.png", "--ui-smoke-case=ready"],
])
def test_acceptance_error_path_uses_the_full_option_value(arguments):
    assert argument_value(arguments, "--ui-smoke") == "C:/test with spaces/page.png"
    assert argument_value(arguments, "--self-test") is None


def test_invalid_update_plan_cannot_choose_report_destination(tmp_path):
    from installer.app import report_update_failure
    path = tmp_path / "plan.json"
    path.write_text('{"nonce":"wrong"}')
    assert report_update_failure(["--finish-update=" + str(path), "--update-nonce=" + "a" * 32], RuntimeError("fixture"))
    assert not (tmp_path / "result.json").exists()


def test_frozen_update_helper_exits_with_no_unhandled_error_dialog(tmp_path, monkeypatch):
    import ctypes
    import runpy
    from pathlib import Path
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["Windows-MCP.exe", "--apply-update=" + str(tmp_path / "missing.json")])
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *args: pytest.fail("Update helper opened an error dialog"))
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "installer/app.py"), run_name="__main__")
    assert result.value.code == 1


@pytest.mark.parametrize("stream", [False, True])
def test_native_helper_dispatch_skips_the_mcp_server_import(monkeypatch, stream):
    from installer import app
    from windows_mcp.vision import native_worker

    calls = []
    monkeypatch.setattr(sys, "argv", ["Windows-MCP.exe", "native-helper", *(["--stream"] if stream else [])])
    monkeypatch.setattr(app, "restore_stdio", lambda: calls.append("stdio"))
    monkeypatch.setattr(native_worker, "main", lambda **kwargs: calls.append(kwargs))
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "windows_mcp.__main__", "Private helper imported the entire MCP server"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    app.main()
    assert calls == ["stdio", {"stream": stream}]


def test_native_helper_rejects_unrecognized_arguments(monkeypatch):
    from installer import app
    from windows_mcp.vision import native_worker

    monkeypatch.setattr(sys, "argv", ["Windows-MCP.exe", "native-helper", "--unknown"])
    monkeypatch.setattr(app, "restore_stdio", lambda: None)
    monkeypatch.setattr(native_worker, "main", lambda **_: pytest.fail("Invalid helper arguments were executed"))
    with pytest.raises(SystemExit) as error:
        app.main()
    assert error.value.code == 2
