"""Outbound origin authorization is checked before DNS, redirects and subrequests."""

import json
from types import SimpleNamespace

import pytest

from tests import test_policy_enforcement
from windows_mcp.infrastructure.network import NetworkGrants, origin
from windows_mcp.infrastructure.policy import PolicyBlocked

secured = test_policy_enforcement.secured


@pytest.mark.parametrize("url,expected", [("https://Example.COM:443/path?q=private", "https://example.com"),
    ("http://example.com:8080/x", "http://example.com:8080"), ("https://[::1]:443/", "https://[::1]"),
    ("https://例子.测试/", "https://xn--fsqu00a.xn--0zwm56d")])
def test_origin_normalization(url, expected):
    assert origin(url) == expected


@pytest.mark.parametrize("url", ["file:///C:/private", "https://user:pass@example.com", "https://example.com\\@evil.test/",
    "https://a%2eexample.com/", "https://example.com:0", "https://example.com/\nprivate", "https://*.example.com"])
def test_ambiguous_urls_are_rejected(url):
    with pytest.raises(ValueError):
        origin(url)


async def test_new_site_needs_independent_local_approval_and_is_session_bound(secured, monkeypatch):
    runtime, mcp, _ = secured
    monkeypatch.setattr("windows_mcp.infrastructure.operator_ui.notify_operator", lambda _: False)
    args = {"url": "https://example.com/read?q=owned", "use_sampling": False}
    owner = runtime.session("local").session_id
    runtime.policy.trust.grant(session_id=owner, applications=["provider:browser"])
    with pytest.raises(PolicyBlocked, match="Network origin"):
        runtime.policy.authorize(owner, "Scrape", args)
    request = await mcp.tools["RequestApproval"](tool="Scrape", arguments=args, description="Read the requested website")
    assert request["network_origins"] == ["https://example.com"]
    with pytest.raises(PolicyBlocked):
        runtime.policy.check_network(owner, args["url"])
    runtime.policy.approvals.approve(request["request_id"])
    runtime.policy.check_network(owner, args["url"])
    for session, url in [("other-client", args["url"]), (owner, "http://example.com"),
                         (owner, "https://example.com:8443"), (owner, "https://evil.example.com")]:
        with pytest.raises(PolicyBlocked):
            runtime.policy.check_network(session, url)
    runtime.policy.network.revoke(owner)
    with pytest.raises(PolicyBlocked):
        runtime.policy.check_network(owner, args["url"])


def test_network_grants_expire_and_tampering_is_detected(tmp_path, monkeypatch):
    store = NetworkGrants(tmp_path)
    store.grant("owner", ["https://example.com"], ttl=60)
    assert store.allows("owner", "https://example.com/path")
    row = store.status()[0]
    with monkeypatch.context() as clock:
        clock.setattr("windows_mcp.infrastructure.network.time.time", lambda: row["expires_at"] + 1)
        assert not store.allows("owner", "https://example.com")
    record = json.loads(store.path.read_text())
    record["payload"]["grants"][0]["origin"] = "https://evil.test"
    store.path.write_text(json.dumps(record))
    assert not store.allows("owner", "https://evil.test")


def test_scrape_checks_each_redirect_before_dns_or_http(secured, monkeypatch):
    runtime, _, _ = secured
    from windows_mcp.desktop import service

    requests, dns = [], []
    monkeypatch.setattr(service, "validate_url", lambda url, **kwargs: dns.append(url))
    monkeypatch.setattr(service.requests, "get", lambda url, **kwargs: requests.append(url) or SimpleNamespace(
        is_redirect=True, headers={"Location": "https://unapproved.test/?data=private"}))
    with runtime.bind("local") as session:
        runtime.policy.network.grant(session.session_id, ["https://approved.test"])
        with pytest.raises(PolicyBlocked, match="unapproved.test"):
            service.Desktop.scrape(SimpleNamespace(), "https://approved.test/page")
    assert requests == dns == ["https://approved.test/page"]


async def test_browser_checks_origin_before_dns_even_when_r2_trust_is_active(secured, monkeypatch):
    runtime, _, _ = secured
    from windows_mcp.browser import playwright_backend

    monkeypatch.setattr(playwright_backend, "validate_url", lambda *args, **kwargs: pytest.fail("Unapproved URL must not reach DNS"))
    backend = playwright_backend.PlaywrightBackend(network_policy=runtime.policy)
    with pytest.raises(PolicyBlocked, match="Network origin"):
        await backend._url("https://unapproved.test", "owner")


async def test_existing_browser_and_scrape_follow_full_access_enable_and_revoke(secured):
    from windows_mcp.browser.playwright_backend import PlaywrightBackend
    from windows_mcp.desktop.service import Desktop
    from unittest.mock import patch

    runtime, _, _ = secured
    backend = PlaywrightBackend(network_policy=runtime.policy)
    url = "http://127.0.0.1:8765/"
    # Keep this origin independently authorized so revocation must also restore
    # private-address protection, rather than merely rejecting the origin.
    runtime.policy.network.grant("owner", [url])
    with pytest.raises(ValueError, match="addresses are blocked"):
        await backend._url(url, "owner")
    runtime.policy.trust.grant_automatic(full_access=True)
    await backend._url(url, "owner")
    from windows_mcp.tools.semantic import _browser

    opened_while_full = _browser(runtime)
    await opened_while_full._url(url, "owner")
    response = SimpleNamespace(is_redirect=False, text="<p>local service</p>", raise_for_status=lambda: None)
    with runtime.bind("local") as session, patch("windows_mcp.desktop.service.requests.get", return_value=response) as http:
        runtime.policy.network.grant(session.session_id, [url])
        assert "local service" in Desktop.scrape(SimpleNamespace(), url)
        assert http.call_count == 1
        runtime.policy.trust.revoke()
        with pytest.raises(ValueError, match="addresses are blocked"):
            Desktop.scrape(SimpleNamespace(), url)
        assert http.call_count == 1
    with pytest.raises(ValueError, match="addresses are blocked"):
        await backend._url(url, "owner")
    with pytest.raises(ValueError, match="addresses are blocked"):
        await opened_while_full._url(url, "owner")


def test_network_allowlist_round_trips_and_does_not_disable_private_address_protection(tmp_path):
    from windows_mcp.infrastructure.config import WindowsMCPConfig, load_config, write_config

    config = WindowsMCPConfig()
    config.policy.allowed_network_origins = ["https://example.com", "http://127.0.0.1:8080"]
    path = tmp_path / "config.toml"
    write_config(config, path)
    loaded = load_config(path)
    assert loaded.policy == config.policy and not loaded.policy.allow_private_browser
    for value in ["*", "https://example.com/path", "https://example.com:443"]:
        path.write_text(f'[policy]\nallowed_network_origins = ["{value}"]')
        with pytest.raises(ValueError):
            load_config(path)


def test_approval_cli_discloses_and_revokes_network_scope(secured, monkeypatch):
    from click.testing import CliRunner
    from windows_mcp import __main__ as cli
    from windows_mcp.infrastructure.approvals import ApprovalStore

    runtime, _, _ = secured
    monkeypatch.setattr(cli, "CONFIG_DIR", runtime.policy.approvals.directory.parent)
    authority = ApprovalStore(cli.CONFIG_DIR / "approvals")
    request = authority.request("client-A", "Scrape", {"url": "https://example.com"}, 0, "read",
                                network_origins=["https://example.com"])
    runner = CliRunner()
    result = runner.invoke(cli.main, ["approve", request["request_id"], "--yes"])
    assert result.exit_code == 0 and '"network_origins"' in result.output and "https://example.com" in result.output
    assert NetworkGrants(authority.directory).allows("client-A", "https://example.com")
    assert runner.invoke(cli.main, ["network", "--session-id", "client-A", "--revoke", "--yes"]).exit_code == 0
    assert not NetworkGrants(authority.directory).allows("client-A", "https://example.com")
