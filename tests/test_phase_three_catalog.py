"""Fixed profile manifests are generated from actual SDK handshakes, never handwritten."""

import asyncio
import copy
import json

import pytest
from click.testing import CliRunner

from windows_mcp.infrastructure.catalog import export_manifest, snapshot, validate_snapshot
from windows_mcp.infrastructure.config import WindowsMCPConfig, load_config, write_config
from windows_mcp.infrastructure.profiles import PROFILES, select_tools


@pytest.mark.parametrize("profile", ["full", "ui-core", "browser"])
async def test_catalog_contains_complete_schema_and_matches_its_profile(profile):
    manifest = await export_manifest({"manifest_version": "0.4"}, profile)
    responses = manifest["_meta"]["com.microsoft.windows"]["static_responses"]
    validate_snapshot(manifest, await snapshot(profile), profile=profile)
    names = {tool["name"] for tool in responses["tools/list"]["tools"]}
    assert len(names) == 31 if profile == "full" else names == PROFILES[profile]
    assert all("inputSchema" in tool and "annotations" in tool for tool in responses["tools/list"]["tools"])
    assert responses["initialize"]["serverInfo"]["name"] == manifest["name"]
    changed = copy.deepcopy(responses)
    changed["tools/list"]["tools"][0]["inputSchema"] = {"type": "string"}
    with pytest.raises(ValueError, match="differ"):
        validate_snapshot(manifest, changed, profile=profile)


async def test_catalog_generation_preserves_live_dispatch_state():
    from windows_mcp.engine.sessions import get_runtime
    from windows_mcp.tools import snapshot as tools_snapshot

    runtime = get_runtime()
    normalizers, resolver = dict(runtime.normalizers), runtime.scope_resolver
    exports = tools_snapshot.state_tool, tools_snapshot.screenshot_tool
    await snapshot("full")
    assert runtime.normalizers == normalizers and runtime.scope_resolver is resolver
    assert (tools_snapshot.state_tool, tools_snapshot.screenshot_tool) == exports


def test_filters_cannot_expand_profile_or_silently_select_nothing():
    all_names = {"Observe", "Execute", "PowerShell", "Browser", "Session", "RequestApproval", "Wait"}
    assert select_tools("browser", all_names, explicit=["Observe", "PowerShell"]) == ["Observe"]
    with pytest.raises(ValueError, match="empty"):
        select_tools("browser", all_names, explicit=["PowerShell"])


def test_profile_config_roundtrip_and_invalid_value(tmp_path):
    path = tmp_path / "config.toml"
    cfg = WindowsMCPConfig()
    cfg.tools.profile, cfg.tools.exclude = "ui-core", ["App"]
    write_config(cfg, path)
    loaded = load_config(path)
    assert loaded.tools == cfg.tools
    path.write_text('[tools]\nprofile="unrecognized"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="tools.profile"):
        load_config(path)


def test_cli_profile_and_static_guard_validate_before_server_start(tmp_path, monkeypatch):
    from windows_mcp import __main__ as cli

    manifest = asyncio.run(export_manifest({}, "browser"))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda p: WindowsMCPConfig())
    starts = []
    monkeypatch.setattr(cli, "_run_server", lambda **kwargs: starts.append(kwargs))
    result = CliRunner().invoke(cli.main, ["serve", "--profile", "browser", "--static-manifest", str(path)])
    assert result.exit_code == 0, result.output
    assert set(starts[0]["explicit_tools"]) == PROFILES["browser"]
    result = CliRunner().invoke(cli.main, ["serve", "--profile", "ui-core", "--static-manifest", str(path)])
    assert result.exit_code != 0 and "fixed profile" in result.output
    assert len(starts) == 1


def test_connector_config_does_not_invent_odr_ids_or_change_host_settings(tmp_path, monkeypatch):
    from windows_mcp.infrastructure import diagnostics
    from windows_mcp import __main__ as cli

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    monkeypatch.setattr(diagnostics, "connectors", lambda: {"file_explorer": {"available": None, "odr_installed": False}})
    path = tmp_path / "companions.json"
    result = CliRunner().invoke(cli.main, ["connectors", "--output", str(path)])
    assert result.exit_code == 0
    assert json.loads(path.read_text())["file_explorer"]["available"] is None
    assert list(tmp_path.iterdir()) == [path]
