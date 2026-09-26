"""Real-desktop tests: the tools drive a genuine Win32 application and we check what it received.

Opt-in only -- they move the real mouse and type real keys, so they never run by accident:

    set WINDOWS_MCP_DESKTOP_TESTS=1
    uv run pytest tests/desktop -v

Safety: the fixture window is topmost and closes itself after two minutes; every raw coordinate
the tests use is checked to belong to the fixture process right before the tool is called, and
keyboard tools refuse to type unless the fixture window is in the foreground. Keep your hands off
the mouse and keyboard while the suite runs (about a minute).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ENABLED = os.getenv("WINDOWS_MCP_DESKTOP_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}

if not ENABLED:
    collect_ignore_glob = ["test_*.py"]

FIXTURE_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "win32_fixture.py"


class FixtureApp:
    """One running instance of ``tests/fixtures/win32_fixture.py``."""

    def __init__(self, directory: Path, title: str, lifetime: float = 120.0) -> None:
        self.title = title
        self.log = directory / f"{uuid.uuid4().hex}.jsonl"
        self.proc = subprocess.Popen([sys.executable, str(FIXTURE_SCRIPT), str(self.log), title, str(lifetime)])
        deadline = time.monotonic() + 20
        ready = None
        while time.monotonic() < deadline and ready is None and self.proc.poll() is None:
            ready = next((e for e in self.events() if e["ev"] == "ready"), None)
            time.sleep(0.05)
        if ready is None:
            self.close()
            raise RuntimeError(f"fixture did not start (exit code {self.proc.poll()})")
        self.hwnd: int = ready["hwnd"]
        self.pid: int = ready["pid"]
        self.window: list[int] = ready["window"]
        self.canvas: list[int] = ready["canvas"]
        self.controls: dict[str, list[int]] = ready["controls"]
        time.sleep(0.3)
        if self.proc.poll() is not None:
            raise RuntimeError("fixture exited right after start-up")

    def events(self, since: int = 0) -> list[dict]:
        try:
            with open(self.log, encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []
        return lines[since:]

    def mark(self) -> int:
        return len(self.events())

    def wait_for(self, predicate, since: int, timeout: float = 3.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while True:
            found = [event for event in self.events(since) if predicate(event)]
            if found or time.monotonic() >= deadline:
                return found
            time.sleep(0.05)

    def center(self, control: str) -> tuple[int, int]:
        left, top, right, bottom = self.controls[control] if control != "canvas" else self.canvas
        return (left + right) // 2, (top + bottom) // 2

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, **kwargs: object):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


@pytest.fixture(scope="session")
def desktop():
    from windows_mcp.desktop.service import Desktop
    from windows_mcp.runtime import call_on_desktop

    os.environ.setdefault("WINDOWS_MCP_DISABLE_FLASH", "1")
    return call_on_desktop(Desktop)


@pytest.fixture(scope="session")
def tools(desktop):
    from windows_mcp.tools import register_all

    mcp = FakeMCP()
    register_all(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


@pytest.fixture
def call(tools):
    def run(tool_name: str, /, **kwargs):
        return asyncio.run(tools[tool_name](**kwargs))

    return run


@pytest.fixture
def app(tmp_path):
    instance = FixtureApp(tmp_path, f"WMCP Fixture {uuid.uuid4().hex[:6]}")
    yield instance
    instance.close()


@pytest.fixture
def make_app(tmp_path):
    started: list[FixtureApp] = []

    def start(title: str | None = None) -> FixtureApp:
        instance = FixtureApp(tmp_path, title or f"WMCP Fixture {uuid.uuid4().hex[:6]}")
        started.append(instance)
        return instance

    yield start
    for instance in started:
        instance.close()
