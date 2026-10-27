"""Real TextPattern ranges and local edits in the dedicated Win32 fixture only."""

import os
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def text_app(tmp_path):
    framework = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319"
    compiler = framework / "csc.exe"
    if not compiler.exists():
        pytest.skip("WPF test fixture requires the local .NET Framework compiler")
    executable, ready = tmp_path / "WmcpTextFixture.exe", tmp_path / "ready.txt"
    source = Path(__file__).parents[1] / "fixtures/TextFixture.cs"
    command = [str(compiler), "/nologo", "/target:winexe", f"/out:{executable}"]
    command.extend(f"/reference:{framework / 'WPF' / (name + '.dll')}" for name in ("PresentationFramework", "PresentationCore", "WindowsBase"))
    command.append(f"/reference:{framework / 'System.Xaml.dll'}")
    result = subprocess.run([*command, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    title = f"WMCP Text Fixture {uuid.uuid4().hex[:6]}"
    process = subprocess.Popen([str(executable), str(ready), title])
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.05)
        assert ready.exists(), "WPF fixture did not start"
        yield SimpleNamespace(hwnd=int(ready.read_text()), title=title)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(5)


def _field(call, app):
    observation = call("Observe", window=str(app.hwnd))
    return next(node["element_id"] for node in observation["elements"] if node["name"] == "Range editor" and node["role"] == "Edit")


def test_real_text_selection_local_replace_delete_and_append(text_app, call):
    target = _field(call, text_app)
    assert call("Execute", target=target, action="set_value", value="one two one 🌍")["status"] == "VERIFIED"
    selected = call("Execute", target=target, action="select_text", text={"text": "one", "occurrence": 2})
    assert selected["status"] == "VERIFIED", selected["errors"]
    reading = call("Observe", target=target, detail="selection")
    assert reading["elements"][0]["state"]["selection"] == ["one"]
    edited = call("Execute", target=target, action="replace_text", value="三", text={"text": "one", "occurrence": 2})
    assert edited["status"] == "VERIFIED", edited
    appended = call("Execute", target=target, action="append_text", value="!")
    assert appended["status"] == "VERIFIED", appended
    deleted = call("Execute", target=target, action="delete_text", text={"text": "two "})
    assert deleted["status"] == "VERIFIED", deleted
    value = call("Observe", target=target, detail="text")["elements"][0]["state"]["text"]
    assert value == "one 三 🌍!"
    word = call("Observe", target=target, detail="text", text={"unit": "word", "index": 1})
    assert word["elements"][0]["state"]["text"].strip() == "one"


def test_real_watch_reads_control_without_full_observation(text_app, call):
    target = _field(call, text_app)
    result = call("Watch", expected={"kind": "element_state", "target": target, "state": {"value": "one two one"}}, timeout_ms=1000)
    assert result["status"] == "VERIFIED"
    from windows_mcp.engine.sessions import get_runtime

    assert get_runtime().current().targets[target]
