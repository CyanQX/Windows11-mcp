"""Policy configuration and remote transport fail closed without changing old tool schemas."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from windows_mcp.infrastructure.config import WindowsMCPConfig, load_config, write_config


def test_policy_config_round_trips_windows_paths(tmp_path):
    config = WindowsMCPConfig()
    config.policy.workspace_roots = [str(tmp_path / "中文目录")]
    config.policy.allow_r3 = True
    config.policy.allow_private_browser = True
    destination = tmp_path / "config.toml"
    write_config(config, destination)
    result = load_config(destination)
    assert result.policy == config.policy


@pytest.mark.parametrize("body", [
    '[policy]\nallow_r3 = "true"',
    '[policy]\nworkspace_roots = [""]',
    '[policy]\napproval_bypass = true',
    'policy = false',
])
def test_invalid_policy_configuration_is_rejected(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    with pytest.raises(ValueError):
        load_config(path)


def test_insecure_remote_flag_cannot_bypass_hardening(monkeypatch):
    from windows_mcp import __main__ as cli

    monkeypatch.setattr(cli, "discover_config_path", lambda _: None)
    monkeypatch.setattr(cli, "_run_server", lambda **kwargs: pytest.fail("Remote server must not start"))
    result = CliRunner().invoke(cli.main, ["serve", "--transport", "streamable-http", "--host", "0.0.0.0", "--allow-insecure-remote"])
    assert result.exit_code != 0
    assert "authentication, TLS" in result.output


def test_approval_cli_is_an_operator_boundary(tmp_path, monkeypatch):
    from windows_mcp import __main__ as cli
    from windows_mcp.infrastructure.approvals import ApprovalStore

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    store = ApprovalStore(tmp_path / "approvals")
    request = store.request("session", "Click", {"loc": [100, 200]}, 2, "Owned fixture point")
    assert not store.consume("session", "Click", {"loc": [100, 200]})
    result = CliRunner().invoke(cli.main, ["approve", request["request_id"]], input="y\n")
    assert result.exit_code == 0
    assert store.consume("session", "Click", {"loc": [100, 200]})


def test_device_paths_are_rejected(tmp_path):
    from windows_mcp.infrastructure.policy import PolicyGate, PolicyBlocked

    gate = PolicyGate(roots=[tmp_path])
    with pytest.raises(PolicyBlocked):
        gate.check_path(Path(tmp_path) / "NUL.txt")
