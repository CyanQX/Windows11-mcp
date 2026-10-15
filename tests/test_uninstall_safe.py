import json

from installer.models import IntegrationStatus as Status
from tests.installer_helpers import make_service


def uninstall(service, ids):
    plan = service.preview("uninstall", ids)
    return service.apply_plan("uninstall", ids, plan["digest"])


def test_uninstall_uses_independent_owned_targets_and_preserves_shared_runtime(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex", "claude"])
    claude = service.backend.clients["claude"]
    before = claude.config.read_bytes()
    assert uninstall(service, ["codex"])[0]["ok"]
    assert claude.config.read_bytes() == before
    assert service.backend.executable.is_file()
    assert service.detector.inspect("claude").status == Status.INSTALLED_MANAGED
    assert uninstall(service, ["claude"])[0]["ok"]
    assert not service.backend.executable.exists()


def test_all_five_targets_uninstall_without_removing_other_mcp_or_user_files(tmp_path):
    service = make_service(tmp_path)
    assert all(row["ok"] for row in service.install_targets(list(service.backend.clients)))
    for key, profile in service.backend.clients.items():
        extra = profile.skill_root / "my-personal-skill" / "note.txt"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("keep", encoding="utf-8")
        assert uninstall(service, [key])[0]["ok"]
        assert extra.read_text() == "keep"
    assert not service.backend.executable.exists()


def test_unknown_reference_retains_runtime_and_user_changes_are_partial_uninstall(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    profile = service.backend.clients["codex"]
    skill = profile.skill_root / "windows-mcp" / "SKILL.md"
    skill.write_text("user changes", encoding="utf-8")
    other = service.backend.clients["gemini"].config
    other.parent.mkdir(parents=True)
    other.write_text("{broken", encoding="utf-8")
    result = uninstall(service, ["codex"])[0]
    assert not result["ok"]
    assert skill.read_text() == "user changes"
    assert service.backend.executable.exists()


def test_external_adoption_requires_unchanged_confirmed_preview_and_restores_original_on_uninstall(tmp_path):
    service = make_service(tmp_path)
    profile = service.backend.clients["claude"]
    profile.config.parent.mkdir(parents=True)
    profile.config.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}, "windows-mcp": {"command": "external"}}}), encoding="utf-8")
    plan = service.preview("adopt", ["claude"])
    assert service.apply_plan("adopt", ["claude"], plan["digest"])[0]["ok"]
    assert uninstall(service, ["claude"])[0]["ok"]
    actual = json.loads(profile.config.read_text())
    assert actual["mcpServers"]["windows-mcp"]["command"] == "external"
    assert actual["mcpServers"]["other"]["command"] == "keep"
