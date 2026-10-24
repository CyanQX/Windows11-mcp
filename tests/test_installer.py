"""Observable installation/rollback behavior in isolated homes; never live client settings."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("tomlkit")
pytest.importorskip("ruamel.yaml")
pytest.importorskip("json5")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.backend import Configuration, Installer, mcp_entry, profiles


@pytest.fixture
def backend(tmp_path):
    skill = tmp_path / "payload"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: windows-mcp\ndescription: Windows automation\n---\nUse the MCP service.\n", encoding="utf-8")
    executable = tmp_path / "setup.exe"
    executable.write_bytes(b"isolated test executable")
    return Installer(tmp_path / "home", tmp_path / "app", skill, executable, {})


@pytest.mark.parametrize("client", ["claude", "codex", "deepseek", "gemini", "zcode", "trae"])
def test_installs_real_skill_and_correct_client_entry_then_uninstalls(backend, client):
    profile = backend.clients[client]
    backend.install(profile)
    assert (profile.skill_root / "windows-mcp" / "SKILL.md").read_bytes() == (backend.skill / "SKILL.md").read_bytes()
    assert Configuration(profile, profile.config.read_bytes()).get() == mcp_entry(profile, backend.executable)
    assert backend.executable.read_bytes() == backend.source_executable.read_bytes()
    assert backend.uninstall(profile)["retained"] == []
    assert Configuration(profile, profile.config.read_bytes()).get() is None
    assert not (profile.skill_root / "windows-mcp" / "SKILL.md").exists()


@pytest.mark.parametrize("client,initial", [
    ("claude", '{"apiKeyHelper":"keep-command", "mcpServers":{"other":{"command":"keep"},"windows-mcp":{"command":"old"}}}'),
    ("codex", '# preserve comment\nmodel = "keep-model"\n[mcp_servers.other]\ncommand = "keep"\n[mcp_servers.windows-mcp]\ncommand = "old"\n'),
    ("deepseek", '- insert:\n    - id: other\n      name: other-plugin\n      config:\n        cwd: !!js process.cwd()\n'),
    ("opencode", '{// a JSONC comment\n"model":"keep-model", "mcp":{"other":{"type":"local","command":["keep"]}}}'),
    ("zcode", '{// keep comment\n"model":"keep-model", "mcp":{"servers":{"other":{"command":"keep"}},"personal":true}}'),
    ("trae", '{"model":"keep-model","mcpServers":{"other":{"command":"keep"}}}'),
])
def test_preserves_existing_settings_and_restores_previous_windows_mcp_entry(backend, client, initial):
    profile = next(profile for profile in profiles(backend.home, {}, include_legacy=True) if profile.id == client)
    profile.config.parent.mkdir(parents=True)
    profile.config.write_text(initial, encoding="utf-8")
    before = Configuration(profile, initial.encode())
    original = before.get()
    backend.install(profile)
    backend.install(profile)  # an idempotent reinstall must keep the original backup
    assert backend.uninstall(profile)["retained"] == []
    after = Configuration(profile, profile.config.read_bytes())
    assert after.get() == original
    if client == "codex":
        assert after.document["model"] == "keep-model"
        assert "# preserve comment" in profile.config.read_text()
        assert after.document["mcp_servers"]["other"]["command"] == "keep"
    elif client == "deepseek":
        assert after.document[0]["insert"][0]["id"] == "other"
        assert "!!js" in profile.config.read_text()
    elif client == "claude":
        assert after.document["apiKeyHelper"] == "keep-command"
        assert after.document["mcpServers"]["other"]["command"] == "keep"
    elif client == "zcode":
        assert after.document["mcp"]["servers"]["other"]["command"] == "keep"
        assert after.document["mcp"]["personal"] is True
        assert "// keep comment" in profile.config.read_text()
    elif client == "trae":
        assert after.document["model"] == "keep-model"
        assert after.document["mcpServers"]["other"]["command"] == "keep"
    else:
        assert after.document["model"] == "keep-model"
        assert after.document["mcp"]["other"]["command"] == ["keep"]


def test_uninstall_preserves_later_user_changes_to_managed_content(backend):
    profile = backend.clients["claude"]
    backend.install(profile)
    skill = profile.skill_root / "windows-mcp" / "SKILL.md"
    skill.write_text("user-edited skill", encoding="utf-8")
    config = json.loads(profile.config.read_text())
    config["mcpServers"]["windows-mcp"]["args"] = ["--serve", "--debug"]
    profile.config.write_text(json.dumps(config), encoding="utf-8")
    result = backend.uninstall(profile)
    assert len(result["retained"]) == 2
    assert skill.read_text() == "user-edited skill"
    assert json.loads(profile.config.read_text())["mcpServers"]["windows-mcp"]["args"][-1] == "--debug"


def test_invalid_config_is_not_overwritten_and_no_skill_is_installed(backend):
    profile = backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    profile.config.write_text("[broken", encoding="utf-8")
    with pytest.raises(ValueError):
        backend.install(profile)
    assert profile.config.read_text() == "[broken"
    assert not (profile.skill_root / "windows-mcp").exists()
    assert not backend.executable.exists()


def test_environment_overrides_match_official_user_scopes(tmp_path):
    clients = {p.id: p for p in profiles(tmp_path, {"CODEX_HOME": str(tmp_path / "custom-codex"), "DSH_HOME": str(tmp_path / "custom-dsh")})}
    assert clients["codex"].config == tmp_path / "custom-codex" / "config.toml"
    assert clients["codex"].skill_root == tmp_path / ".agents" / "skills"
    assert clients["deepseek"].skill_root == tmp_path / "custom-dsh" / "skills"
    assert clients["deepseek"].config == tmp_path / "custom-dsh" / "cordis.patch.yml"


def test_new_client_profiles_and_trae_existing_region_config(tmp_path):
    clients = {p.id: p for p in profiles(tmp_path, {})}
    assert set(clients) == {"claude", "codex", "deepseek", "gemini", "zcode", "trae"}
    assert clients["zcode"].config == tmp_path / ".zcode/cli/config.json"
    assert clients["zcode"].skill_root == tmp_path / ".zcode/skills"
    international = tmp_path / "AppData/Roaming/Trae/User/mcp.json"
    international.parent.mkdir(parents=True)
    empty_trae = next(profile for profile in profiles(tmp_path, {}) if profile.id == "trae")
    assert empty_trae.config == international
    international.write_text('{"mcpServers":{"personal":{"command":"keep"}}}')
    trae = next(profile for profile in profiles(tmp_path, {}) if profile.id == "trae")
    assert trae.config == international
    assert trae.skill_root == tmp_path / ".trae/skills"


def test_existing_opencode_receipt_is_still_maintainable_without_offering_new_installs(backend):
    assert "opencode" not in backend.clients
    profile = next(item for item in profiles(backend.home, {}, include_legacy=True) if item.id == "opencode")
    backend.install(profile)
    reloaded = Installer(backend.home, backend.data, backend.skill, backend.source_executable, {})
    assert reloaded.uninstall(reloaded.clients["opencode"])["retained"] == []
    assert "opencode" not in Installer(backend.home, backend.data, backend.skill, backend.source_executable, {}).clients


def test_skill_only_does_not_touch_or_validate_unrelated_mcp_settings(backend):
    profile = backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    profile.config.write_text("[broken", encoding="utf-8")
    backend.install(profile, skill=True, mcp=False)
    assert profile.config.read_text() == "[broken"
    assert (profile.skill_root / "windows-mcp" / "SKILL.md").is_file()
    assert not backend.executable.exists()
