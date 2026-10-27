"""Input/output schemas and validation before any provider dispatch."""

import json

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.engine.contracts import normalize_expected
from windows_mcp.tools import register_all
from tests import test_policy_enforcement, test_semantic_engine

secured = test_policy_enforcement.secured
observed = test_semantic_engine.observed


@pytest.mark.parametrize("expected", [
    {"kind": "file_exists"}, {"kind": "file_content", "path": "a"},
    {"kind": "file_sha256", "path": "a", "sha256": "wrong"},
    {"kind": "element_state", "target": "x", "state": {}},
    {"kind": "element_state", "target": "x", "state": {"enabled": "true"}},
    {"kind": "element_state", "target": "x", "state": {"unknown": True}},
    {"kind": "window_exists", "window": ""}, {"kind": "file_exists", "path": "a", "typo": 1}])
def test_invalid_expected_is_rejected(expected):
    with pytest.raises(ValueError):
        normalize_expected(expected)


def test_browser_checked_requires_boolean():
    with pytest.raises(ValueError):
        normalize_expected({"kind": "checked", "value": "true"}, browser=True)
    assert normalize_expected({"kind": "checked", "value": True}, browser=True)["value"] is True


async def test_live_mcp_catalog_exposes_field_level_result_and_expected_schemas():
    mcp = FastMCP("phase-two-schema-probe")
    register_all(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        for name in ("Observe", "Execute", "Verify", "Watch"):
            tool = tools[name]
            output = getattr(tool, "output_schema", None) or tool.outputSchema
            body = json.dumps(output)
            assert "additionalProperties\": true" not in body or "properties" in body
            assert ("observation_id" if name == "Observe" else "status") in body
            assert "audit_id" in body
            assert "result" not in output.get("properties", {})  # Keep the existing top-level response.
        tool = tools["Execute"]
        schema = getattr(tool, "input_schema", None) or tool.inputSchema
        assert "file_sha256" in json.dumps(schema) and "read_only" in json.dumps(schema)
        assert "occurrence" in json.dumps(schema) and "paragraph" in json.dumps(schema)
        result = await client.call_tool("Verify", {"expected": {"kind": "file_content", "path": "missing"}}, raise_on_error=False)
        assert result.is_error


async def test_invalid_expected_fails_before_scope_resolution_or_approval(secured):
    runtime, mcp, _ = secured
    runtime.scope_resolver = lambda tool, *args: (pytest.fail("Invalid expected reached target resolution")
        if tool == "Execute" else {"application": "provider:requestapproval"})
    with pytest.raises(ValueError):
        await mcp.tools["Execute"](target="unused", expected={"kind": "file_content", "path": "unused"})
    with pytest.raises(ValueError):
        await mcp.tools["RequestApproval"](tool="Execute", arguments={"target": "unused", "expected": {"kind": "element_state", "target": "unused", "state": {}}}, description="Invalid fixture condition")
    assert list(runtime.policy.approvals.directory.glob("*.json")) == []


async def test_actual_observe_and_verify_keep_top_level_structured_results(observed, monkeypatch, tmp_path):
    desktop, _, runtime, _, _ = observed
    from windows_mcp.engine import sessions

    monkeypatch.setattr(sessions, "_runtime", runtime)
    mcp = FastMCP("phase-two-wire-probe")
    register_all(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    async with Client(mcp) as client:
        observation = await client.call_tool("Observe", {"window": "100"})
        content = getattr(observation, "structured_content", None) or observation.structuredContent
        assert content["observation_id"] and content["elements"][0]["actions"]
        result = await client.call_tool("Verify", {"expected": {"kind": "file_exists", "path": str(tmp_path / "missing")}})
        content = getattr(result, "structured_content", None) or result.structuredContent
        assert content["status"] == "FAILED" and content["verification"]["passed"] is False
