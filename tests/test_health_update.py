"""Offline release/health failures and shared runtime upgrade invariants."""

import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from installer.health import HealthChecker
from installer.models import ManagerError, IntegrationStatus
from installer.updates import UpdateService, official_url
from tests.installer_helpers import make_service


class Response:
    def __init__(self, value=None, content=None):
        self.value = value
        self.content = json.dumps(value).encode() if content is None else content
        self.text = self.content.decode(errors="replace")

    def json(self):
        return self.value

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, _size):
        yield self.content


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)

    def get(self, *args, **kwargs):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def release(content, digest=True):
    asset = {"name": "Windows-MCP-Setup-1.3.0.exe", "size": len(content), "browser_download_url": "https://github.com/CyanQX/Windows11-mcp/releases/download/v1.3.0/Windows-MCP-Setup-1.3.0.exe"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
    return {"tag_name": "v1.3.0", "draft": False, "prerelease": False, "assets": [asset], "body": "Release notes"}


@pytest.mark.parametrize("url", ["http://github.com/CyanQX/Windows11-mcp/releases/download/a/b", "https://github.com/evil/Windows11-mcp/releases/download/a/b", "https://github.com.evil.test/CyanQX/Windows11-mcp/releases/download/a/b", "https://github.com/CyanQX/Windows11-mcp/releases/download/a/b?token=x"])
def test_update_source_scope(url):
    with pytest.raises(ManagerError, match="UPDATE_SOURCE_INVALID"):
        official_url(url)


def test_download_hash_size_and_current_preserved(tmp_path):
    content = b"MZ" + b"x" * 2048
    service = UpdateService(tmp_path, "1.2.1", Session(Response(release(content)), Response(content=content)))
    plan = service.check()
    target = service.download(plan)
    assert target.read_bytes() == content
    current = tmp_path / "current.exe"
    current.write_bytes(b"MZ old")
    path, nonce = service.prepare(target, plan, current)
    assert json.loads(path.read_text())["old_sha256"] == hashlib.sha256(b"MZ old").hexdigest()
    assert len(nonce) == 32 and current.read_bytes() == b"MZ old"


def test_missing_checksum_forbids_update(tmp_path):
    service = UpdateService(tmp_path, "1.2.1", Session(Response(release(b"MZ" + b"x" * 2048, False))))
    with pytest.raises(ManagerError, match="UPDATE_CHECKSUM_MISSING"):
        service.check()
    assert not (tmp_path / "updates").exists()


@pytest.mark.parametrize("content", [b"MZ" + b"tampered" * 256, b"MZshort", b"XX" + b"x" * 2048])
def test_tampered_download_never_promoted(tmp_path, content):
    good = b"MZ" + b"x" * 2048
    service = UpdateService(tmp_path, "1.2.1", Session(Response(release(good)), Response(content=content)))
    plan = service.check()
    with pytest.raises(ManagerError):
        service.download(plan)
    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.partial"))


def test_update_network_failure(tmp_path):
    service = UpdateService(tmp_path, "1.2.1", Session(requests.Timeout()))
    with pytest.raises(ManagerError, match="UPDATE_CHECK_FAILED"):
        service.check()


def test_update_helper_failed_launch_rolls_back_without_killing(tmp_path, monkeypatch):
    import sys
    from installer import updates
    content = b"MZ" + b"x" * 2048
    service = UpdateService(tmp_path, "1.2.1", Session(Response(release(content)), Response(content=content)))
    plan = service.check()
    staged = service.download(plan)
    current = tmp_path / "current.exe"
    current.write_bytes(b"MZ previous")
    path, nonce = service.prepare(staged, plan, current)
    monkeypatch.setattr(sys, "executable", str(staged))
    monkeypatch.setattr(updates.psutil, "pid_exists", lambda _: False)
    launched = []
    def launch(args, **kwargs):
        launched.append(args)
        return SimpleNamespace(poll=lambda: 1)
    monkeypatch.setattr(updates.subprocess, "Popen", launch)
    assert updates.apply_update(path, nonce) is False
    assert current.read_bytes() == b"MZ previous"
    assert json.loads(path.read_text())["state"] == "rolled_back"
    assert len(launched) == 2
    assert launched[0] == [str(current), f"--finish-update={path}", f"--update-nonce={nonce}"]


@pytest.mark.parametrize("change", ["staged", "target"])
def test_update_plan_rejects_alternate_program_or_cache_target(tmp_path, change):
    from installer.updates import load_plan
    content = b"MZ" + b"x" * 2048
    service = UpdateService(tmp_path, "1.2.1", Session(Response(release(content)), Response(content=content)))
    metadata = service.check()
    staged = service.download(metadata)
    current = tmp_path / "current.exe"
    current.write_bytes(b"MZ previous")
    path, nonce = service.prepare(staged, metadata, current)
    row = json.loads(path.read_text())
    row[change] = str(path.parent / ("other.exe" if change == "staged" else "target.exe"))
    path.write_text(json.dumps(row))
    with pytest.raises(ManagerError, match="UPDATE_PLAN_INVALID"):
        load_plan(path, nonce)
    assert current.read_bytes() == b"MZ previous"


def test_shared_runtime_requires_explicit_global_upgrade(tmp_path):
    service = make_service(tmp_path)
    assert service.install_targets(["codex", "claude"])[0]["ok"]
    before = service.backend.executable.read_bytes()
    service.backend.source_executable.write_bytes(b"MZ new release")
    with pytest.raises(ManagerError, match="SHARED_UPGRADE_REQUIRED"):
        service.install_targets(["gemini"])
    assert service.backend.executable.read_bytes() == before
    plan = service.preview("upgrade", ["codex", "claude"])
    result = service.apply_plan("upgrade", ["codex", "claude"], plan["digest"])
    assert all(row["ok"] for row in result)
    assert all(service.detector.inspect(key).status == IntegrationStatus.INSTALLED_MANAGED for key in ("codex", "claude"))


def test_failed_global_upgrade_restores_every_target(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    service.install_targets(["codex", "claude"])
    paths = set(service.paths("codex") + service.paths("claude"))
    before = {path: path.read_bytes() for path in paths}
    service.backend.source_executable.write_bytes(b"MZ new release")
    original = service.backend.install

    def broken(profile):
        original(profile)
        if profile.id == "claude":
            raise OSError("simulated disk write failure")
    monkeypatch.setattr(service.backend, "install", broken)
    plan = service.preview("upgrade", ["codex", "claude"])
    with pytest.raises(ManagerError, match="UPGRADE_ROLLED_BACK"):
        service.apply_plan("upgrade", ["codex", "claude"], plan["digest"])
    assert all(path.read_bytes() == content for path, content in before.items())


def test_global_upgrade_snapshot_can_restore_each_client_entry(tmp_path):
    from installer.backend import Configuration, atomic_write
    service = make_service(tmp_path)
    service.install_targets(["codex", "claude"])
    plan = service.preview("upgrade", ["codex", "claude"])
    result = service.apply_plan("upgrade", ["codex", "claude"], plan["digest"])
    transaction = result[0]["transaction_id"]
    for key in ("codex", "claude"):
        profile = service.backend.clients[key]
        config = Configuration(profile, profile.config.read_bytes())
        expected = config.get()
        config.set(None)
        atomic_write(profile.config, config.encode())
        restore = service.preview_restore(transaction, key)
        assert restore["target"] == key
        service.restore(transaction, restore["digest"], key)
        assert Configuration(profile, profile.config.read_bytes()).get() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [("missing", "RUNTIME_MISSING"), ("timeout", "MCP_TIMEOUT"), ("list", "TOOL_LIST_MISMATCH"), ("session", "SESSION_NOT_READY"), ("version", "VERSION_MISMATCH"), ("success", "VERIFIED")])
async def test_health_truthful_failures(tmp_path, monkeypatch, case, code):
    import fastmcp
    import asyncio
    executable = tmp_path / "runtime.exe"
    if case != "missing":
        executable.write_bytes(b"MZ")
    manifest = {"version": "1.2.1", "tools": [{"name": "Session"}]}

    class Client:
        initialize_result = SimpleNamespace(serverInfo=SimpleNamespace(version="other" if case == "version" else "1.2.1"))

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            if case == "timeout":
                await asyncio.sleep(1)
            return self

        async def __aexit__(self, *args):
            pass

        async def list_tools(self):
            return [SimpleNamespace(name="Different" if case == "list" else "Session")]

        async def call_tool(self, *args):
            return SimpleNamespace(is_error=case == "session", data={"status": "READY"})
    monkeypatch.setattr(fastmcp, "Client", Client)
    result = await HealthChecker(manifest, 0.03).probe(executable)
    assert result.code == code
    assert result.ok == (case == "success")
