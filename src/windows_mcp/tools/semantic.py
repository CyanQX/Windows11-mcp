"""High-level tools with typed results; legacy signatures remain available."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Literal

from fastmcp import Context
from fastmcp.utilities.types import Image

from windows_mcp.engine.action_router import execute
from windows_mcp.engine.approval_flow import request_approval
from windows_mcp.engine.contracts import (ActionResult, Status, ObservationResponse,
    WatchResult, Expected, BrowserExpected, TextRangeSpec)
from windows_mcp.engine.observe_router import observe_desktop
from windows_mcp.engine.session_status import run_session_operation
from windows_mcp.engine.sessions import get_runtime
from windows_mcp.engine.verifier import verify
from windows_mcp.native_api import registry as providers
from windows_mcp.runtime import run_on_desktop
from windows_mcp.tools._common import declare_tool
from windows_mcp.engine.watch import watch


def _browser(runtime):
    if runtime.browser is None:
        from windows_mcp.browser.playwright_backend import PlaywrightBackend

        runtime.browser = PlaywrightBackend(network_policy=runtime.policy)
    return runtime.browser


def register(mcp, *, get_desktop, get_analytics):
    tool = partial(declare_tool, mcp, get_analytics)

    @tool("Observe", description="Read a bounded semantic observation with source/session/revision. UIA is default; playwright reads only this client's managed page. target scopes a subtree; mode=refresh reads known targets only, without checking for new/removed controls. detail=text/selection reads bounded TextPattern data for explicit targets. since_revision returns scope-specific deltas; refresh_targets means refresh target IDs before acting. strategy=cached opts into two-level UIA cache traversal. Screen coordinates and browser CSS pixels are separate.", access="read", title=False, analytics="Observe-Tool", output_schema=ObservationResponse.model_json_schema())
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

    @tool("Execute", description="Act on a server-issued Observe target. auto follows Session.interaction_mode: semantic prefers supported UIA patterns; pointer sends real mouse input for click/double_click/right_click/hover, rechecking the pinned control after movement before each press. Explicit semantic/physical modes override the server setting. dispatch reports accepted native events and UIA provider calls; input_dispatched never implies business success. Results distinguish VERIFIED from DISPATCHED_UNVERIFIED. Supply a typed business postcondition in expected. select_text/replace_text/delete_text accept text range options (literal + optional one-based occurrence, or provider unit/index/count); append_text uses document end. Local edits require verified selection/focus and physical input. This tool never retries dispatched input automatically.", access="write", title=False, open_world=True, analytics="Execute-Tool", output_schema=ActionResult.model_json_schema())
    def execute_tool(target: str, action: str = "click", value: str | None = None,
                     mode: Literal["auto", "semantic", "physical", "visual"] = "auto",
                     expected: Expected | None = None, approval_token: str | None = None,
                     text: TextRangeSpec | None = None, ctx: Context = None) -> dict:
        runtime = get_runtime()
        result = execute(get_desktop(), runtime, runtime.current(), target=target, action=action,
                         value=value, mode=mode, expected=expected, text=text)
        return ActionResult.model_validate(result).model_dump(mode="json")

    @tool("Verify", description="Independently verify file_exists/file_content/file_sha256, element_state or window_exists. File checks obey workspace scope. Unknown postconditions fail; UI changes alone are not business evidence.", access="read", title=False, analytics="Verify-Tool", output_schema=ActionResult.model_json_schema())
    def verify_tool(expected: Expected, ctx: Context = None) -> dict:
        runtime = get_runtime()
        evidence = verify(get_desktop(), runtime, runtime.current(), expected)
        return ActionResult(status=Status.VERIFIED if evidence.passed is True else Status.FAILED,
                            verification=evidence).model_dump(mode="json")

    @tool("Watch", description="Wait up to 30 seconds for scoped UIA state changes or a typed postcondition. UIA events wake live reads; a 1.5-second fallback handles dropped events. element_state reads only the pinned control. Property events refresh known targets; structure/overflow triggers a bounded scan. Events alone never verify success. Cancellation removes subscriptions.", access="read", title=False, analytics="Watch-Tool", output_schema=WatchResult.model_json_schema())
    async def watch_tool(window: str | None = None, expected: Expected | None = None,
                         events: list[str] | None = None, timeout_ms: int = 10000,
                         ctx: Context = None) -> dict:
        runtime = get_runtime()
        result = await watch(get_desktop(), runtime, runtime.current(), window=window,
                             expected=expected, events=events, timeout_ms=timeout_ms)
        return WatchResult.model_validate(result).model_dump(mode="json")

    @tool("Browser", description="Start/status/close an isolated Playwright browser; navigate/new_page/observe/read managed pages; click/fill/check/select semantic targets. No user browser profile, CDP/evaluate, credentials or arbitrary JS API. Every mutation requires the same operator approval as desktop actions; use expected for independent postconditions.", access="write", title=False, open_world=True, analytics="Browser-Tool")
    async def browser_tool(operation: Literal["start", "status", "close", "navigate", "new_page", "observe", "read", "click", "fill", "check", "select"],
                           page_id: str | None = None, target: str | None = None, url: str | None = None,
                           value: str | None = None, expected: BrowserExpected | None = None, limit: int = 100,
                           timeout_ms: int = 10000, channel: Literal["chromium", "msedge", "chrome"] = "msedge",
                           headless: bool = False, approval_token: str | None = None, ctx: Context = None) -> dict:
        runtime = get_runtime()
        return await _browser(runtime).run(runtime.current().session_id, operation, page_id=page_id,
            target=target, url=url, value=value, expected=expected, limit=limit, timeout_ms=timeout_ms,
            channel=channel, headless=headless)

    @tool("NativeAPI", description="Whitelisted native filesystem provider: read/info/write UTF-8 files inside operator-configured workspace roots. New files use exclusive creation; overwrite needs explicit approval and overwrite=True. Writes are read back and verified. No dynamic provider registration or shell/COM execution.", access="write", title=False, open_world=True, analytics="NativeAPI-Tool", offload="background")
    def native_tool(operation: Literal["read", "info", "write"], path: str, provider: str = "filesystem",
                    content: str | None = None, overwrite: bool = False,
                    approval_token: str | None = None, ctx: Context = None) -> dict:
        return providers.run(get_runtime().policy, provider, operation, path=path, content=content, overwrite=overwrite)

    @tool("Session", description="Read this client's state, persistent or scoped trust, network grants and shared pending UIA action. trust_mode=automatic with installation scope covers all connected clients/apps and HTTP(S) origins until manually disabled, across reconnects/restarts; configured capability/path limits still apply. Cancel stops cooperative Wait/WaitFor/input/Watch; it cannot undo dispatched input or terminate UIA helpers. After pending_action becomes COMPLETED, independently verify the effect, then acknowledge with its action_id before new desktop mutations. Resume never acknowledges it. Only the owning session may acknowledge. Session ids are server-generated.", access="write", title=False, open_world=True, analytics="Session-Tool")
    async def session_tool(operation: Literal["status", "cancel", "resume", "acknowledge"] = "status",
                           action_id: str | None = None, ctx: Context = None) -> dict:
        return await run_session_operation(get_runtime(), get_runtime().current(), operation, action_id)

    @tool("RequestApproval", description="Request local human approval for exact tool arguments and a server-derived application/window scope. If persistent automatic mode is enabled locally, returns TRUSTED without a prompt: all connected clients/apps and HTTP(S) origins remain authorized across reconnects/restarts until manually disabled. Full access (Session.full_access) permits high-risk tools and files outside configured roots under the Windows account; signed approval storage and input-takeover checks remain protected. Legacy automatic grants retain configured limits. Otherwise use one-use approval or matching legacy scoped R2 trust. New HTTP origins need local approval outside persistent mode. Clicking 持续全自动 is a human-only settings action; this tool cannot approve or enable it. Inspect Session.trust_scope and execute the original call directly after TRUSTED.", access="write", title=False, open_world=True, analytics="RequestApproval-Tool", offload="background")
    def request_approval_tool(tool: str, arguments: dict, description: str, ctx: Context = None) -> dict:
        return request_approval(get_runtime(), tool, arguments, description)

    @tool("Visual", description="Window-bound image fallback. auto tries HWND Windows.Graphics.Capture, isolated PrintWindow, then foreground desktop crop. A serialized native capture process is reused, with job-wide timeout cleanup. Window capture may observe covered/background windows; physical clicks require foreground, hit-testing and local pixel-patch revalidation before pressing. Changed content requires a new observation; no automatic relocation/replay. region is an unscaled image ROI; max_width bounds transfer. Optional local OCR returns image-pixel word boxes and availability diagnostics. previous_target/only_if_changed omit duplicate image encoding/transfer, not fresh capture. Pixel agreement is not control identity or business verification. Declare fallback_reason.", access="read", title=False, analytics="Visual-Tool")
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
