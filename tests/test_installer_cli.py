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
