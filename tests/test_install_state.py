import json

import pytest

from installer.detection import ClientDiscovery
from installer.models import IntegrationStatus as Status, ManagerError, Presence, SelectionModel
from tests.installer_helpers import make_service


def test_fresh_desktop_codex_is_the_only_default_and_installed_targets_cannot_be_selected(tmp_path):
    service = make_service(tmp_path)
    selection = SelectionModel()
    selection.refresh(service.inspect_all())
    assert selection.selected == {"codex"}
    assert service.install_targets(["codex"])[0]["ok"]
    selection.refresh(service.inspect_all())
    assert selection.selected == set()
    assert selection.choose("codex") is False
    selection.select_all()
    assert "codex" not in selection.selected
    assert service.detector.inspect("codex").status == Status.INSTALLED_MANAGED


def test_backend_rejects_reinstall_without_changing_config_receipt_or_backups(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    profile = service.backend.clients["codex"]
    before = {path: path.read_bytes() for path in (profile.config, service.backend.receipt_path(profile))}
    transactions = service.backups()
    with pytest.raises(ManagerError, match="INSTALL_REJECTED"):
        service.install_targets(["codex"])
    assert {path: path.read_bytes() for path in before} == before
    assert service.backups() == transactions


@pytest.mark.parametrize("missing", ["skill", "mcp", "runtime"])
def test_missing_integration_parts_never_become_plain_installable(tmp_path, missing):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    profile = service.backend.clients["codex"]
    if missing == "skill":
        (profile.skill_root / "windows-mcp" / "SKILL.md").unlink()
    elif missing == "mcp":
        profile.config.write_text("# preserved\n[mcp_servers.other]\ncommand='keep'\n", encoding="utf-8")
    else:
        service.backend.executable.unlink()
    state = service.detector.inspect("codex")
    assert state.status == (Status.BROKEN if missing == "runtime" else Status.PARTIAL)
    assert not state.install_eligible
    with pytest.raises(ManagerError, match="INSTALL_REJECTED"):
        service.install_targets(["codex"])


def test_external_and_invalid_configuration_are_preserved(tmp_path):
    service = make_service(tmp_path)
    profile = service.backend.clients["codex"]
    profile.config.parent.mkdir(parents=True)
    external = "[mcp_servers.windows-mcp]\ncommand='external'\n"
    profile.config.write_text(external, encoding="utf-8")
    assert service.detector.inspect("codex").status == Status.EXTERNAL
    with pytest.raises(ManagerError):
        service.install_targets(["codex"])
    assert profile.config.read_text() == external
    profile.config.write_text("[broken", encoding="utf-8")
    assert service.detector.inspect("codex").status == Status.BROKEN
    with pytest.raises(ManagerError):
        service.install_targets(["codex"])
    assert profile.config.read_text() == "[broken"


def test_codex_cli_on_path_does_not_count_as_a_desktop_install(tmp_path, monkeypatch):
    discovery = ClientDiscovery(tmp_path)
    monkeypatch.setattr(discovery, "desktop_apps", lambda: {})
    monkeypatch.setattr("installer.detection.shutil.which", lambda _: "codex.exe")
    service = make_service(tmp_path / "case", discovery=discovery)
    state = service.detector.inspect("codex")
    assert state.presence == Presence.NOT_FOUND
    assert not state.install_eligible
    selection = SelectionModel()
    selection.refresh(service.inspect_all())
    assert selection.selected == set()  # never silently select another tool


def test_unsafe_receipt_cannot_expand_owned_skill_or_backup_scope(tmp_path):
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    profile = service.backend.clients["codex"]
    path = service.backend.receipt_path(profile)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["files"]["../../auth.json"] = {"hash": "bad", "backup": None}
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert service.detector.inspect("codex").status == Status.BROKEN
    with pytest.raises(ManagerError):
        service.preview("uninstall", ["codex"])
