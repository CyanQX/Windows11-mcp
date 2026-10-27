"""Schema drift and invalid readiness data are different from optional backend absence."""

from types import SimpleNamespace
import copy

import pytest
from mcp.types import Tool

from installer.health import HealthChecker
from windows_mcp.infrastructure.catalog import wire_model


@pytest.mark.parametrize("case,code", [("success", "VERIFIED"), ("schema", "TOOL_SCHEMA_MISMATCH"),
    ("invalid_schema", "TOOL_SCHEMA_INVALID"), ("initialize", "INITIALIZE_MISMATCH"),
    ("result", "SESSION_RESULT_INVALID")])
async def test_health_checks_full_contract_before_reporting_ready(tmp_path, monkeypatch, case, code):
    import fastmcp

    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"MZ")
    tool = Tool.model_validate({"name": "Session", "inputSchema": {"type": "object"},
                               "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}}})
    from importlib.metadata import version
    current = version("windows-mcp")
    initialized = {"serverInfo": {"name": "windows-mcp", "version": current}}
    expected = {"initialize": initialized, "tools/list": {"tools": [wire_model(tool)]}}
    manifest = {"version": current, "tools": [{"name": "Session"}],
                "_meta": {"com.microsoft.windows": {"static_responses": copy.deepcopy(expected)}}}
    if case == "schema":
        manifest["_meta"]["com.microsoft.windows"]["static_responses"]["tools/list"]["tools"][0]["description"] = "changed"
    if case == "invalid_schema":
        tool = Tool.model_validate({"name": "Session", "inputSchema": {"type": "invalid"}})
    class Client:
        initialize_result = SimpleNamespace(serverInfo=SimpleNamespace(version=current),
            model_dump=lambda **kw: {} if case == "initialize" else initialized)
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def list_tools(self):
            return [tool]
        async def call_tool(self, *args):
            return SimpleNamespace(is_error=False, data={"status": "READY", "session_id": 2 if case == "result" else "sess_fixture",
                "native_components": {"windows_ocr": {"available": False}, "tesseract": {"available": False}}})
    monkeypatch.setattr(fastmcp, "Client", Client)
    result = await HealthChecker(manifest).probe(executable)
    assert result.code == code
    if case == "success":
        assert result.schema_checked and result.result_checked and result.ok
        assert result.native_components and result.warnings
