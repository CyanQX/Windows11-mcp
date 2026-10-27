"""Non-interactive regressions for cancellation, late UIA effects and atomic writes."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests import test_policy_enforcement
from tests.test_wait_for_tool import _state
from windows_mcp.desktop import elements
from windows_mcp.engine.pending import ActionInProgress
from windows_mcp.native_api import filesystem
from windows_mcp.runtime import run_on_desktop

secured = test_policy_enforcement.secured


@pytest.mark.parametrize("duration", [-1, 0, 61, 3600, True, 0.5])
async def test_wait_rejects_unbounded_or_invalid_duration(secured, duration):
    _, mcp, _ = secured
    with pytest.raises(ValueError):
        await mcp.tools["Wait"](duration=duration)


async def test_wait_releases_desktop_and_can_be_cancelled(secured):
    _, mcp, _ = secured
    waiting = asyncio.create_task(mcp.tools["Wait"](duration=1))
    try:
        await asyncio.sleep(0.02)
        assert await asyncio.wait_for(run_on_desktop(lambda: "available"), 0.3) == "available"
        assert not waiting.done()
        await mcp.tools["Session"](operation="cancel")
        with pytest.raises(RuntimeError, match="CANCELLED"):
            await asyncio.wait_for(waiting, 0.3)
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


async def test_cancel_then_immediate_resume_does_not_revive_an_old_wait(secured):
    _, mcp, _ = secured
    waiting = asyncio.create_task(mcp.tools["Wait"](duration=1))
    try:
        await asyncio.sleep(0.02)
        await mcp.tools["Session"](operation="cancel")
        await mcp.tools["Session"](operation="resume")
        with pytest.raises(RuntimeError, match="CANCELLED"):
            await asyncio.wait_for(waiting, 0.3)
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


async def test_wait_for_releases_desktop_between_probes_and_cancels(secured):
    runtime, mcp, _ = secured
    from windows_mcp.tools.input import register

    calls = []
    desktop = SimpleNamespace(get_state=lambda **kwargs: calls.append(True) or _state(active_window_name="Loading"))
    from tests.test_policy_enforcement import FakeMCP
    from windows_mcp.tools.gateway import ToolGateway

    isolated = FakeMCP()
    register(ToolGateway(isolated, runtime=runtime), get_desktop=lambda: desktop, get_analytics=lambda: None)
    waiting = asyncio.create_task(isolated.tools["WaitFor"](condition="active_window", window_name="Ready", timeout=2, interval=1))
    try:
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls
        assert await asyncio.wait_for(run_on_desktop(lambda: "available"), 0.3) == "available"
        await mcp.tools["Session"](operation="cancel")
        with pytest.raises(RuntimeError, match="CANCELLED"):
            await asyncio.wait_for(waiting, 0.3)
        assert len(calls) == 1
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.parametrize("late_error", [False, True])
async def test_late_uia_effects_block_new_input_until_finished_and_acknowledged(secured, late_error):
    runtime, mcp, _ = secured
    release = threading.Event()
    effects = []

    def action():
        release.wait(3)
        effects.append("late effect")
        if late_error:
            raise OSError("private provider detail")
        return "private result"

    try:
        with runtime.bind("local"):
            with pytest.raises(ActionInProgress) as exc:
                await run_on_desktop(elements.call_with_timeout, action, 0.02)
        action_id = exc.value.action_id
        state = await mcp.tools["Session"]()
        assert state["pending_action"]["state"] == "RUNNING"
        with pytest.raises(ActionInProgress):
            await mcp.tools["Shortcut"](shortcut="enter")
        with pytest.raises(ActionInProgress):
            await mcp.tools["Session"](operation="acknowledge", action_id=action_id)
        with pytest.raises(ValueError):
            runtime.pending.acknowledge("another-client", action_id)
        await mcp.tools["Session"](operation="cancel")
        assert runtime.pending.status()["state"] == "RUNNING"
        release.set()
        for _ in range(100):
            if runtime.pending.status()["state"] == "COMPLETED":
                break
            await asyncio.sleep(0.01)
        completed = (await mcp.tools["Session"]())["pending_action"]
        assert effects == ["late effect"] and completed["state"] == "COMPLETED"
        assert completed["error_type"] == ("OSError" if late_error else None)
        assert "private" not in str(completed)
        await mcp.tools["Session"](operation="resume")
        with pytest.raises(ActionInProgress):
            await mcp.tools["Shortcut"](shortcut="enter")
        assert (await mcp.tools["Session"](operation="acknowledge", action_id=action_id))["pending_action"] is None
    finally:
        release.set()


def test_completed_uia_calls_do_not_leave_pending_state(secured):
    runtime, _, _ = secured
    with runtime.bind("local"):
        assert elements.call_with_timeout(lambda: 42) == (True, 42)
        assert runtime.pending.status() is None
        with pytest.raises(ValueError, match="provider"):
            elements.call_with_timeout(lambda: (_ for _ in ()).throw(ValueError("provider")))
        assert runtime.pending.status() is None


@pytest.mark.parametrize("failure", ["fsync", "replace", "verify"])
def test_failed_atomic_overwrite_preserves_original_and_removes_temporary(secured, monkeypatch, failure):
    runtime, _, workspace = secured
    path = workspace / "owned.txt"
    path.write_bytes(b"original")
    def fail(*args):
        raise OSError("injected disk/write failure")
    if failure == "verify":
        original_read = filesystem.Path.read_bytes
        monkeypatch.setattr(filesystem.Path, "read_bytes", lambda self: b"corrupt" if self.suffix == ".tmp" else original_read(self))
    else:
        if failure == "fsync":
            monkeypatch.setattr(filesystem.os, "fsync", fail)
        else:
            monkeypatch.setattr(filesystem, "replace_verified", fail)
    with pytest.raises(OSError):
        filesystem.run(runtime.policy, "write", str(path), content="replacement", overwrite=True)
    assert path.read_bytes() == b"original"
    assert list(workspace.iterdir()) == [path]


def test_atomic_overwrite_preserves_exact_unicode_and_exclusive_creation(secured):
    runtime, _, workspace = secured
    path = workspace / "owned.txt"
    assert filesystem.run(runtime.policy, "write", str(path), content="old")["status"] == "VERIFIED"
    with pytest.raises(FileExistsError):
        filesystem.run(runtime.policy, "write", str(path), content="must not replace")
    assert path.read_bytes() == b"old"
    assert filesystem.run(runtime.policy, "write", str(path), content="中文🌍\r\nnext\n", overwrite=True)["status"] == "VERIFIED"
    assert path.read_bytes().decode("utf-8") == "中文🌍\r\nnext\n"


def test_windows_atomic_overwrite_preserves_explicit_dacl(secured):
    import win32security

    runtime, _, workspace = secured
    path = workspace / "protected.txt"
    path.write_text("original")
    dacl = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)
    def security():
        descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
        return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            descriptor, win32security.SDDL_REVISION_1, win32security.DACL_SECURITY_INFORMATION)
    before = security()
    assert filesystem.run(runtime.policy, "write", str(path), content="updated", overwrite=True)["status"] == "VERIFIED"
    assert security() == before


async def test_cancel_resume_does_not_revive_an_already_authorized_queued_action(secured):
    runtime, mcp, _ = secured
    from tests.test_policy_enforcement import FakeMCP
    from windows_mcp.tools.gateway import ToolGateway
    from windows_mcp.infrastructure.authorization import check_dispatch

    isolated = FakeMCP()
    gateway = ToolGateway(isolated, runtime=runtime)
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    @gateway.tool(name="Click")
    async def action(loc: list[int]):
        entered.set()
        await release.wait()
        check_dispatch()
        effects.append(loc)
    arguments = {"loc": [1, 2]}
    request = runtime.policy.approvals.request(runtime.session("local").session_id, "Click", arguments, 2,
        "isolated queued action", authorization_scope={"application": "unresolved"})
    runtime.policy.approvals.approve(request["request_id"])
    waiting = asyncio.create_task(isolated.tools["Click"](**arguments))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await mcp.tools["Session"](operation="cancel")
        await mcp.tools["Session"](operation="resume")
        release.set()
        with pytest.raises(RuntimeError, match="CANCELLED"):
            await waiting
        assert effects == []
    finally:
        release.set()
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


async def test_execute_reports_pending_dispatch_without_verification_or_retry(secured, monkeypatch):
    from windows_mcp.engine import action_router
    from tests.test_elements import make

    runtime, _, _ = secured
    release = threading.Event()
    attempts = []
    monkeypatch.setattr(action_router, "resolve", lambda *args: make(capabilities=frozenset({"value"})))
    def act(*args, **kwargs):
        attempts.append(True)
        elements.call_with_timeout(lambda: release.wait(3), timeout=0.01)
        pytest.fail("A pending action must not return an ActOutcome")
    monkeypatch.setattr(elements, "act", act)
    try:
        with runtime.bind("local") as session:
            result = action_router.execute(None, runtime, session, target="owned-target", action="set_value", value="owned")
        assert result["status"] == "DISPATCHED_UNVERIFIED" and result["input_dispatched"]
        assert result["pending_action_id"] == runtime.pending.status()["action_id"]
        assert result["verification"]["passed"] is None and not result["retryable"] and attempts == [True]
        release.set()
        for _ in range(100):
            if runtime.pending.status()["state"] == "COMPLETED":
                break
            await asyncio.sleep(0.01)
    finally:
        release.set()


def test_windows_partial_replacement_failure_restores_original(secured, monkeypatch):
    import ctypes
    from pathlib import Path

    runtime, _, workspace = secured
    path = workspace / "recover.txt"
    path.write_bytes(b"original")
    def partial_failure(destination, temporary, backup, *args):
        filesystem.os.replace(Path(destination), Path(backup))
        ctypes.set_last_error(1177)  # Documented ERROR_UNABLE_TO_MOVE_REPLACEMENT_2.
        return False
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(ReplaceFileW=partial_failure))
    with pytest.raises(OSError):
        filesystem.run(runtime.policy, "write", str(path), content="replacement", overwrite=True)
    assert path.read_bytes() == b"original" and list(workspace.iterdir()) == [path]
