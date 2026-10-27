"""Actual bounded MCP handshakes, separate from static installation state."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    code: str
    message: str
    checked_at: str
    elapsed_ms: int
    tool_count: int = 0
    server_version: str | None = None
    session_status: str | None = None
    schema_checked: bool = False
    result_checked: bool = False
    native_components: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def session_data(result) -> dict:
    for name in ("data", "structured_content", "structuredContent"):
        value = getattr(result, name, None)
        if isinstance(value, dict):
            return value
    for block in getattr(result, "content", []):
        if getattr(block, "type", None) == "text":
            try:
                value = json.loads(block.text)
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    return {}


class HealthChecker:
    def __init__(self, manifest: dict, timeout: float = 60, *, arguments=None, home: Path | None = None):
        self.manifest, self.timeout = manifest, timeout
        self.arguments = ["--serve"] if arguments is None else arguments
        self.home = home

    async def probe(self, executable: Path, *, progress=None) -> HealthResult:
        started = time.monotonic()
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        def result(ok, code, message, **data):
            return HealthResult(ok, code, message, now, int((time.monotonic() - started) * 1000), **data)

        if not executable.is_file():
            return result(False, "RUNTIME_MISSING", "MCP 服务程序不存在")
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from windows_mcp.infrastructure.catalog import initialize_client_options

        try:
            async with asyncio.timeout(self.timeout):
                env = {"ANONYMIZED_TELEMETRY": "false", "WINDOWS_MCP_WATCHDOG": "off", "WINDOWS_MCP_DISABLE_FLASH": "1"}
                if self.home:
                    # Acceptance probes isolate core config and audit files as well
                    # as client configs. Normal GUI verification keeps the real home.
                    self.home.mkdir(parents=True, exist_ok=True)
                    env.update(USERPROFILE=str(self.home), HOMEDRIVE=self.home.drive, HOMEPATH=str(self.home)[len(self.home.drive):], APPDATA=str(self.home / "AppData" / "Roaming"), LOCALAPPDATA=str(self.home / "AppData" / "Local"))
                transport = StdioTransport(command=str(executable), args=self.arguments, env=env)
                async with Client(transport, timeout=self.timeout, **initialize_client_options()) as client:
                    if progress:
                        progress(0.3, "MCP stdio 初始化握手已完成")
                    tools = await client.list_tools()
                    expected = {item["name"] for item in self.manifest["tools"]}
                    if {tool.name for tool in tools} != expected:
                        return result(False, "TOOL_LIST_MISMATCH", "实际工具清单与当前版本不一致", tool_count=len(tools))
                    static = self.manifest.get("_meta", {}).get("com.microsoft.windows", {}).get("static_responses")
                    if static:
                        from jsonschema import Draft202012Validator
                        from windows_mcp.infrastructure.catalog import wire_model

                        if wire_model(client.initialize_result) != static.get("initialize"):
                            return result(False, "INITIALIZE_MISMATCH", "初始化能力或协议响应与发行清单不一致", tool_count=len(tools))
                        actual = [wire_model(tool) for tool in tools]
                        try:
                            for tool in actual:
                                Draft202012Validator.check_schema(tool["inputSchema"])
                                if tool.get("outputSchema"):
                                    Draft202012Validator.check_schema(tool["outputSchema"])
                        except Exception:
                            return result(False, "TOOL_SCHEMA_INVALID", "服务返回了无效的工具模式", tool_count=len(tools))
                        if actual != static.get("tools/list", {}).get("tools"):
                            return result(False, "TOOL_SCHEMA_MISMATCH", "工具参数、结果模式或说明与发行清单不一致", tool_count=len(tools))
                    if progress:
                        progress(0.6, "实际工具清单已逐项核对")
                    response = await client.call_tool("Session", {"operation": "status"})
                    state = session_data(response)
                    if response.is_error or state.get("status") != "READY":
                        return result(False, "SESSION_NOT_READY", "MCP 会话未就绪", tool_count=len(tools), session_status=state.get("status"))
                    if static:
                        schema = next(item.get("outputSchema") for item in actual if item["name"] == "Session")
                        try:
                            if schema:
                                Draft202012Validator(schema).validate(state)
                            if not isinstance(state.get("session_id"), str) or not state["session_id"].startswith("sess_"):
                                raise ValueError("Invalid session identity")
                        except Exception:
                            return result(False, "SESSION_RESULT_INVALID", "Session 结果未通过模式与身份核对", schema_checked=True)
                    if progress:
                        progress(0.85, "Session READY 已确认，正在核对服务版本")
                    info = getattr(client.initialize_result, "server_info", None) or client.initialize_result.serverInfo
                    version = info.version
                    if version != self.manifest["version"]:
                        return result(False, "VERSION_MISMATCH", "服务版本与安装助手期望版本不一致，请通过版本与升级处理", tool_count=len(tools), server_version=version, session_status="READY")
                    if progress:
                        progress(1, "MCP 版本、工具清单与会话状态均已核验")
                    native = state.get("native_components")
                    warnings = []
                    if native and not (native.get("windows_ocr", {}).get("available") or native.get("tesseract", {}).get("available")):
                        warnings.append("可选本地 OCR 不可用；基础桌面操作仍可使用，详细条件见 doctor")
                    return result(True, "VERIFIED", "最近一次 MCP 连接验证通过", tool_count=len(tools), server_version=version,
                                  session_status="READY", schema_checked=bool(static), result_checked=bool(static),
                                  native_components=native, warnings=warnings)
        except TimeoutError:
            return result(False, "MCP_TIMEOUT", "连接验证超时，检查进程已关闭")
        except Exception as exc:
            # Details can be added to a private redacted report, not dumped on MCP stdout.
            return result(False, "MCP_CONNECTION_FAILED", f"无法完成 MCP 连接验证（{type(exc).__name__}）")
