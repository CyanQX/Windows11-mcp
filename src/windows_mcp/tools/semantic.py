"""High-level tools with typed results; legacy signatures remain available."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastmcp import Context
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations

from windows_mcp.engine.action_router import execute
from windows_mcp.engine.contracts import (ActionResult, Status, ObservationResponse,
    WatchResult, Expected, BrowserExpected, TextRangeSpec)
from windows_mcp.engine.observe_router import observe_desktop
from windows_mcp.engine.sessions import get_runtime
from windows_mcp.engine.verifier import verify
from windows_mcp.infrastructure import with_analytics
from windows_mcp.native_api import registry as providers
from windows_mcp.runtime import run_on_desktop, call_on_desktop
from windows_mcp.engine.watch import watch

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def _browser(runtime):
    if runtime.browser is None:
        from windows_mcp.browser.playwright_backend import PlaywrightBackend

        runtime.browser = PlaywrightBackend(allow_private=runtime.policy.allow_private_browser, network_policy=runtime.policy)
    return runtime.browser


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(name="Observe", description="Read a bounded semantic observation with source/session/revision. UIA is default; playwright reads only this client's managed page. target scopes a subtree; mode=refresh reads known targets only, without checking for new/removed controls. detail=text/selection reads bounded TextPattern data for explicit targets. since_revision returns scope-specific deltas; refresh_targets means refresh target IDs before acting. strategy=cached opts into two-level UIA cache traversal. Screen coordinates and browser CSS pixels are separate.", annotations=_READ, output_schema=ObservationResponse.model_json_schema())
    @with_analytics(get_analytics(), "Observe-Tool")
    async def observe_tool(window: str | None = None, source: Literal["auto", "uia", "playwright"] = "auto",
                           page_id: str | None = None, limit: int = 100, max_depth: int = 8,
                           include_offscreen: bool = False, since_revision: int | None = None,
                           target: str | None = None, targets: list[str] | None = None,
                           mode: Literal["full", "refresh"] = "full",
                           detail: Literal["control", "text", "selection"] = "control",
                           text: TextRangeSpec | None = None, max_chars: int = 4000,
                           strategy: Literal["bounded", "cached"] = "bounded",
                           ctx: Context = None) -> dict:
        runtime, session = get_runtime(), get_runtime().current()
        if source == "playwright" or (source == "auto" and page_id):
            if target or targets or text or mode != "full" or detail != "control":
                raise ValueError("Target refresh/subtree/text modes currently require source=uia")
            result = await _browser(runtime).run(session.session_id, "observe", page_id=page_id, limit=limit)
            return ObservationResponse.model_validate(result).model_dump(mode="json", exclude={"status"})
        try:
            result = await asyncio.wait_for(run_on_desktop(observe_desktop, get_desktop(), session,
                window=window, limit=limit, max_depth=max_depth, include_offscreen=include_offscreen,
                since_revision=since_revision, target=target, targets=targets, mode=mode,
                detail=detail, text=text, max_chars=max_chars, strategy=strategy), timeout=10.0)
            return ObservationResponse.model_validate(result).model_dump(mode="json", exclude={"status"})
        except TimeoutError:
            return ObservationResponse(status="TIMEOUT", diagnostics=["UIA provider exceeded the observation deadline; no action was dispatched"]).model_dump(mode="json")

    @mcp.tool(name="Execute", description="Act on a server-issued Observe target. auto chooses supported UIA patterns then physical input; semantic/physical can be explicit. Results distinguish VERIFIED from DISPATCHED_UNVERIFIED. Supply a typed business postcondition in expected. select_text/replace_text/delete_text accept text range options (literal + optional one-based occurrence, or provider unit/index/count); append_text uses document end. Local edits require verified selection/focus and physical input. This tool never retries dispatched input automatically.", annotations=_WRITE, output_schema=ActionResult.model_json_schema())
    @with_analytics(get_analytics(), "Execute-Tool")
    def execute_tool(target: str, action: str = "click", value: str | None = None,
                     mode: Literal["auto", "semantic", "physical", "visual"] = "auto",
                     expected: Expected | None = None, approval_token: str | None = None,
                     text: TextRangeSpec | None = None, ctx: Context = None) -> dict:
        runtime = get_runtime()
        result = execute(get_desktop(), runtime, runtime.current(), target=target, action=action,
                         value=value, mode=mode, expected=expected, text=text)
        return ActionResult.model_validate(result).model_dump(mode="json")

    @mcp.tool(name="Verify", description="Independently verify file_exists/file_content/file_sha256, element_state or window_exists. File checks obey workspace scope. Unknown postconditions fail; UI changes alone are not business evidence.", annotations=_READ, output_schema=ActionResult.model_json_schema())
    @with_analytics(get_analytics(), "Verify-Tool")
    def verify_tool(expected: Expected, ctx: Context = None) -> dict:
        runtime = get_runtime()
        evidence = verify(get_desktop(), runtime, runtime.current(), expected)
        return ActionResult(status=Status.VERIFIED if evidence.passed is True else Status.FAILED,
                            verification=evidence).model_dump(mode="json")

    @mcp.tool(name="Watch", description="Wait up to 30 seconds for scoped UIA state changes or a typed postcondition. UIA events wake live reads; a 1.5-second fallback handles dropped events. element_state reads only the pinned control. Property events refresh known targets; structure/overflow triggers a bounded scan. Events alone never verify success. Cancellation removes subscriptions.", annotations=_READ, output_schema=WatchResult.model_json_schema())
    @with_analytics(get_analytics(), "Watch-Tool")
    async def watch_tool(window: str | None = None, expected: Expected | None = None,
                         events: list[str] | None = None, timeout_ms: int = 10000,
                         ctx: Context = None) -> dict:
        runtime = get_runtime()
        result = await watch(get_desktop(), runtime, runtime.current(), window=window,
                             expected=expected, events=events, timeout_ms=timeout_ms)
        return WatchResult.model_validate(result).model_dump(mode="json")

    @mcp.tool(name="Browser", description="Start/status/close an isolated Playwright browser; navigate/new_page/observe/read managed pages; click/fill/check/select semantic targets. No user browser profile, CDP/evaluate, credentials or arbitrary JS API. Every mutation requires the same operator approval as desktop actions; use expected for independent postconditions.", annotations=_WRITE)
    @with_analytics(get_analytics(), "Browser-Tool")
    async def browser_tool(operation: Literal["start", "status", "close", "navigate", "new_page", "observe", "read", "click", "fill", "check", "select"],
                           page_id: str | None = None, target: str | None = None, url: str | None = None,
                           value: str | None = None, expected: BrowserExpected | None = None, limit: int = 100,
                           timeout_ms: int = 10000, channel: Literal["chromium", "msedge", "chrome"] = "msedge",
                           headless: bool = False, approval_token: str | None = None, ctx: Context = None) -> dict:
        runtime = get_runtime()
        return await _browser(runtime).run(runtime.current().session_id, operation, page_id=page_id,
            target=target, url=url, value=value, expected=expected, limit=limit, timeout_ms=timeout_ms,
            channel=channel, headless=headless)

    @mcp.tool(name="NativeAPI", description="Whitelisted native filesystem provider: read/info/write UTF-8 files inside operator-configured workspace roots. New files use exclusive creation; overwrite needs explicit approval and overwrite=True. Writes are read back and verified. No dynamic provider registration or shell/COM execution.", annotations=_WRITE)
    @with_analytics(get_analytics(), "NativeAPI-Tool", offload="background")
    def native_tool(operation: Literal["read", "info", "write"], path: str, provider: str = "filesystem",
                    content: str | None = None, overwrite: bool = False,
                    approval_token: str | None = None, ctx: Context = None) -> dict:
        return providers.run(get_runtime().policy, provider, operation, path=path, content=content, overwrite=overwrite)

    @mcp.tool(name="Session", description="Read this client's state, scoped trust, network grants and shared pending UIA action. Cancel stops cooperative Wait/WaitFor/input/Watch; it cannot undo dispatched input or terminate UIA helpers. After pending_action becomes COMPLETED, independently verify the effect, then acknowledge with its action_id before new desktop mutations. Resume never acknowledges it. Only the owning session may acknowledge. Session ids are server-generated.", annotations=_WRITE)
    @with_analytics(get_analytics(), "Session-Tool")
    async def session_tool(operation: Literal["status", "cancel", "resume", "acknowledge"] = "status",
                           action_id: str | None = None, ctx: Context = None) -> dict:
        runtime, session = get_runtime(), get_runtime().current()
        if operation == "cancel":
            session.cancel()
            if runtime.browser:
                await runtime.browser.close_session(session.session_id)
        elif operation == "resume":
            session.cancelled.clear()
        elif operation == "acknowledge":
            runtime.pending.acknowledge(session.session_id, action_id)
        trust = runtime.policy.trust.status() if runtime.policy.trust else None
        if trust and trust.get("session_id") != session.session_id:
            trust = None
        from windows_mcp.vision.native_bridge import capabilities
        native = await asyncio.to_thread(capabilities) if operation == "status" else {"probe": "skipped_for_control_operation"}
        return {"status": Status.CANCELLED.value if session.cancelled.is_set() else "READY",
                "native_components": native,
                "session_id": session.session_id, "revision": session.revision,
                "workspace_roots": [str(root) for root in runtime.policy.roots],
                "r3_enabled": runtime.policy.allow_r3, "pending_action": runtime.pending.status(),
                "trust_mode": (trust or {}).get("mode"), "trust_scope": trust,
                "network_grants": runtime.policy.network.status(session.session_id),
                "allowed_network_origins": sorted(runtime.policy.allowed_network_origins)}

    @mcp.tool(name="RequestApproval", description="Request local human approval for exact tool arguments and a server-derived application/window scope. 全自动 covers only the displayed client/application for up to eight hours; inspect Session.trust_scope. New HTTP origins require separate local approval, including Scrape and Browser navigation; approving a displayed origin permits this session to access it for up to eight hours. TRUSTED means the original call fits the existing R2 scope. This tool cannot approve; R3 always needs one-use approval.", annotations=_WRITE)
    @with_analytics(get_analytics(), "RequestApproval-Tool", offload="background")
    def request_approval_tool(tool: str, arguments: dict, description: str, ctx: Context = None) -> dict:
        runtime = get_runtime()
        normalized = runtime.normalize(tool, arguments)
        scope = call_on_desktop(runtime.scope_resolver, tool, normalized, runtime.current())
        risk = runtime.policy.risk(tool, normalized, scope)
        network_origins = runtime.policy.network_origins(tool, normalized)
        if risk == 3 and not runtime.policy.allow_r3:
            raise PermissionError("BLOCKED_BY_POLICY: R3 is disabled by the local operator")
        if risk == 2 and runtime.policy.trusted(runtime.current().session_id, tool, normalized, scope) and not network_origins:
            return {"status": "TRUSTED", "risk": risk, "approval_required": False,
                    "authorization_scope": scope,
                    "operator_guidance": "本机操作员已授权此客户端会话和应用范围：可直接调用匹配工具；其他应用和出网站点仍单独授权。"}
        request = runtime.policy.approvals.request(runtime.current().session_id, tool, normalized, risk, description,
                                                  authorization_scope=scope, network_origins=network_origins)
        from windows_mcp.infrastructure.operator_ui import notify_operator
        notified = notify_operator(request["request_id"])
        return {"status": "NEEDS_APPROVAL", "request_id": request["request_id"], "risk": risk,
                "expires_at": request["expires_at"], "scope": request["scope"],
                "authorization_scope": scope, "network_origins": network_origins,
                "operator_ui_opened": notified,
                "operator_guidance": "由本地操作员核对会话、应用、参数和出网站点。勾选「全自动」仅覆盖所示会话与应用，最长8小时；出网站点单独确认。AI不能代为批准或勾选。",
                "operator_command": f"windows-mcp approve {request['request_id']}"}

    @mcp.tool(name="Visual", description="Window-bound image fallback. auto tries HWND Windows.Graphics.Capture, isolated PrintWindow, then foreground desktop crop. Window capture may observe covered/background windows; physical clicks still require foreground and hit-testing. region is an unscaled image ROI; max_width bounds transfer. Optional local OCR returns image-pixel word boxes and availability diagnostics. previous_target/only_if_changed reduce duplicate images; pixel change is not business verification. Declare fallback_reason.", annotations=_READ)
    @with_analytics(get_analytics(), "Visual-Tool")
    async def visual_tool(window: str, fallback_reason: Literal["no_semantic_target", "canvas", "provider_failure"],
                    backend: Literal["auto", "wgc", "printwindow", "desktop"] = "auto",
                    region: list[int] | None = None, max_width: int = 1600,
                    previous_target: str | None = None, only_if_changed: bool = False,
                    change_threshold: float = 0.0, ocr: bool = False,
                    ocr_backend: Literal["auto", "windows_ocr", "tesseract"] = "auto",
                    ocr_language: str | None = None, ctx: Context = None):
        from windows_mcp.vision.adapter import capture_async

        metadata, data = await capture_async(get_desktop(), get_runtime().current(), window, fallback_reason,
            backend=backend, region=region, max_width=max_width, previous_target=previous_target,
            only_if_changed=only_if_changed, change_threshold=change_threshold,
            ocr=ocr, ocr_backend=ocr_backend, ocr_language=ocr_language)
        result = [json.dumps(metadata, ensure_ascii=False)]
        if data is not None:
            result.append(Image(data=data, format="png"))
        return result
