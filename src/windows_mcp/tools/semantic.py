"""High-level tools with typed results; legacy signatures remain available."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

from fastmcp import Context
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations

from windows_mcp.engine.action_router import execute
from windows_mcp.engine.contracts import ActionResult, Status
from windows_mcp.engine.observe_router import observe_desktop
from windows_mcp.engine.sessions import get_runtime
from windows_mcp.engine.verifier import verify
from windows_mcp.infrastructure import with_analytics
from windows_mcp.native_api import registry as providers
from windows_mcp.runtime import run_on_desktop
from windows_mcp.watchdog.subscription import SubscriptionHub

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def _browser(runtime):
    if runtime.browser is None:
        from windows_mcp.browser.playwright_backend import PlaywrightBackend

        runtime.browser = PlaywrightBackend(allow_private=runtime.policy.allow_private_browser)
    return runtime.browser


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(name="Observe", description="Read a bounded semantic observation with source/session/revision. UIA is default; playwright reads only this client's managed page. since_revision returns a delta; refresh_targets means obtain a full observation before acting. Screen coordinates and browser CSS pixels are separate.", annotations=_READ)
    @with_analytics(get_analytics(), "Observe-Tool")
    async def observe_tool(window: str | None = None, source: Literal["auto", "uia", "playwright"] = "auto",
                           page_id: str | None = None, limit: int = 100, max_depth: int = 8,
                           include_offscreen: bool = False, since_revision: int | None = None,
                           ctx: Context = None) -> dict:
        runtime, session = get_runtime(), get_runtime().current()
        if source == "playwright" or (source == "auto" and page_id):
            return await _browser(runtime).run(session.session_id, "observe", page_id=page_id, limit=limit)
        try:
            return await asyncio.wait_for(run_on_desktop(observe_desktop, get_desktop(), session,
                window=window, limit=limit, max_depth=max_depth, include_offscreen=include_offscreen,
                since_revision=since_revision), timeout=10.0)
        except TimeoutError:
            return {"status": Status.TIMEOUT.value, "diagnostics": ["UIA provider exceeded the observation deadline; no action was dispatched"]}

    @mcp.tool(name="Execute", description="Act on a server-issued Observe target. auto chooses supported UIA patterns then physical input; semantic/physical can be explicit. Results distinguish VERIFIED from DISPATCHED_UNVERIFIED. Supply a business postcondition in expected. This tool never retries dispatched input automatically.", annotations=_WRITE)
    @with_analytics(get_analytics(), "Execute-Tool")
    def execute_tool(target: str, action: str = "click", value: str | None = None,
                     mode: Literal["auto", "semantic", "physical", "visual"] = "auto",
                     expected: dict | None = None, approval_token: str | None = None,
                     ctx: Context = None) -> dict:
        runtime = get_runtime()
        return execute(get_desktop(), runtime, runtime.current(), target=target, action=action,
                       value=value, mode=mode, expected=expected)

    @mcp.tool(name="Verify", description="Independently verify file_exists/file_content/file_sha256, element_state or window_exists. File checks obey workspace scope. Unknown postconditions fail; UI changes alone are not business evidence.", annotations=_READ)
    @with_analytics(get_analytics(), "Verify-Tool")
    def verify_tool(expected: dict, ctx: Context = None) -> dict:
        runtime = get_runtime()
        evidence = verify(get_desktop(), runtime, runtime.current(), expected)
        return ActionResult(status=Status.VERIFIED if evidence.passed is True else Status.FAILED,
                            verification=evidence).model_dump()

    @mcp.tool(name="Watch", description="Wait up to 30 seconds for scoped UIA changes or a postcondition. Events are hints; periodically read back state so dropped events cannot hang a request. Concurrent subscribers are independent, expire and are removed on cancellation; queue overflow is reported.", annotations=_READ)
    @with_analytics(get_analytics(), "Watch-Tool")
    async def watch_tool(window: str | None = None, expected: dict | None = None,
                         events: list[str] | None = None, timeout_ms: int = 10000,
                         ctx: Context = None) -> dict:
        if not 100 <= timeout_ms <= 30000:
            raise ValueError("timeout_ms must be 100..30000")
        runtime, session = get_runtime(), get_runtime().current()
        deadline = time.monotonic() + timeout_ms / 1000
        watched_ref = None
        if expected and expected.get("kind") == "element_state":
            record = session.targets.get(expected.get("target"))
            if not record:
                return {"status": Status.TARGET_STALE.value, "diagnostics": ["Observe the watched control first"]}
            watched_ref = record["ref"]
            if window is None:
                window = str(record["handle"])
        try:
            initial = await asyncio.wait_for(run_on_desktop(observe_desktop, get_desktop(), session, window=window, limit=100), timeout=timeout_ms / 1000)
        except TimeoutError:
            return {"status": Status.TIMEOUT.value, "diagnostics": ["UIA observation exceeded the Watch deadline"]}
        handle = int(initial["context"]["window_id"])
        hub = runtime.watchdog.subscriptions if runtime.watchdog else SubscriptionHub()
        subscription = hub.subscribe(session.session_id, handle, events or ["focus", "structure", "property"], timeout_ms / 1000)
        all_events, overflow_seen = [], False
        try:
            while time.monotonic() < deadline:
                if session.cancelled.is_set():
                    return {"status": Status.CANCELLED.value, "subscription_id": subscription.subscription_id}
                items, overflow = hub.drain(subscription)
                all_events.extend(items)
                overflow_seen |= overflow or len(all_events) > 64
                all_events = all_events[-64:]
                current = await asyncio.wait_for(run_on_desktop(observe_desktop, get_desktop(), session, window=str(handle), limit=100), timeout=max(0.01, deadline - time.monotonic()))
                predicate = expected
                if watched_ref:
                    live_target = next((item["element_id"] for item in current["elements"] if item["element_id"].rsplit(":", 1)[-1] == watched_ref), None)
                    if not live_target:
                        return {"status": Status.TARGET_STALE.value, "diagnostics": ["The watched control disappeared; observe again"]}
                    predicate = {**expected, "target": live_target}
                evidence = await asyncio.wait_for(run_on_desktop(verify, get_desktop(), runtime, session, predicate), timeout=max(0.01, deadline - time.monotonic())) if predicate else None
                if (evidence and evidence.passed is True) or (expected is None and current["revision"] != initial["revision"]):
                    return {"status": Status.VERIFIED.value, "subscription_id": subscription.subscription_id,
                            "revision": current["revision"], "events": all_events, "overflow": overflow_seen,
                            "verification": evidence.model_dump() if evidence else {"passed": True, "evidence_type": "semantic_state_change"},
                            "observation": current}
                await asyncio.sleep(min(0.2 if items else 0.4, max(0, deadline - time.monotonic())))
            return {"status": Status.TIMEOUT.value, "subscription_id": subscription.subscription_id,
                    "revision": session.revision, "events": all_events, "overflow": overflow_seen,
                    "diagnostics": ["Condition did not match within the deadline; event silence is not evidence of success"]}
        except TimeoutError:
            return {"status": Status.TIMEOUT.value, "subscription_id": subscription.subscription_id,
                    "events": all_events, "overflow": overflow_seen}
        except RuntimeError:
            if session.cancelled.is_set():
                return {"status": Status.CANCELLED.value, "subscription_id": subscription.subscription_id}
            raise
        finally:
            hub.cancel(subscription.subscription_id, session.session_id)

    @mcp.tool(name="Browser", description="Start/status/close an isolated Playwright browser; navigate/new_page/observe/read managed pages; click/fill/check/select semantic targets. No user browser profile, CDP/evaluate, credentials or arbitrary JS API. Every mutation requires the same operator approval as desktop actions; use expected for independent postconditions.", annotations=_WRITE)
    @with_analytics(get_analytics(), "Browser-Tool")
    async def browser_tool(operation: Literal["start", "status", "close", "navigate", "new_page", "observe", "read", "click", "fill", "check", "select"],
                           page_id: str | None = None, target: str | None = None, url: str | None = None,
                           value: str | None = None, expected: dict | None = None, limit: int = 100,
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

    @mcp.tool(name="Session", description="Read this client's session state or cancel/resume it. Cancel blocks subsequent actions and stops cooperative input/Watch waits. Browser contexts can be closed explicitly. Session ids are generated by the server and cannot be selected by an agent.", annotations=_WRITE)
    @with_analytics(get_analytics(), "Session-Tool")
    async def session_tool(operation: Literal["status", "cancel", "resume"] = "status", ctx: Context = None) -> dict:
        runtime, session = get_runtime(), get_runtime().current()
        if operation == "cancel":
            session.cancelled.set()
            if runtime.browser:
                await runtime.browser.close_session(session.session_id)
        elif operation == "resume":
            session.cancelled.clear()
        return {"status": Status.CANCELLED.value if session.cancelled.is_set() else "READY",
                "session_id": session.session_id, "revision": session.revision,
                "workspace_roots": [str(root) for root in runtime.policy.roots],
                "r3_enabled": runtime.policy.allow_r3,
                "trust_mode": (runtime.policy.trust.status() or {}).get("mode") if runtime.policy.trust else None}

    @mcp.tool(name="RequestApproval", description="Request local human approval for an exact tool/argument call. The operator can check 全自动 in the review window and approve to authorize subsequent R2 actions until revoked; unchecked approves only this call. With active Session trust_mode, call R2 action tools directly. TRUSTED means proceed without a review window. This tool cannot grant permission; R3 still requires per-call approval.", annotations=_WRITE)
    @with_analytics(get_analytics(), "RequestApproval-Tool", offload="background")
    def request_approval_tool(tool: str, arguments: dict, description: str, ctx: Context = None) -> dict:
        runtime = get_runtime()
        normalized = runtime.normalize(tool, arguments)
        risk = runtime.policy.risk(tool, normalized)
        if risk == 3 and not runtime.policy.allow_r3:
            raise PermissionError("BLOCKED_BY_POLICY: R3 is disabled by the local operator")
        if risk == 2 and runtime.policy.trust and runtime.policy.trust.allows():
            return {"status": "TRUSTED", "risk": risk, "approval_required": False,
                    "operator_guidance": "本机操作员已开启完全授权：可直接调用该工具，无需逐次审批。"}
        request = runtime.policy.approvals.request(runtime.current().session_id, tool, normalized, risk, description)
        from windows_mcp.infrastructure.operator_ui import notify_operator
        notified = notify_operator(request["request_id"])
        return {"status": "NEEDS_APPROVAL", "request_id": request["request_id"], "risk": risk,
                "expires_at": request["expires_at"], "scope": request["scope"],
                "operator_ui_opened": notified,
                "operator_guidance": "由本地操作员在本机操作审批窗口确认：勾选「全自动」并批准后，后续 R2 操作无需逐次审批；不勾选则仅批准本次操作。AI 不能代为批准或勾选。",
                "operator_command": f"windows-mcp approve {request['request_id']}"}

    @mcp.tool(name="Visual", description="Capture only a foreground target window when semantic observation cannot locate the target. Declare fallback_reason=no_semantic_target/canvas/provider_failure. Returns a screenshot and a short-lived window/process/geometry-bound visual target. Execute(mode='visual',target=...,action='click',value='[image_x,image_y]') rechecks identity, geometry, foreground and occlusion before approved physical input.", annotations=_READ)
    @with_analytics(get_analytics(), "Visual-Tool")
    def visual_tool(window: str, fallback_reason: Literal["no_semantic_target", "canvas", "provider_failure"],
                    ctx: Context = None):
        from windows_mcp.vision.adapter import capture

        metadata, data = capture(get_desktop(), get_runtime().current(), window, fallback_reason)
        return [json.dumps(metadata, ensure_ascii=False), Image(data=data, format="png")]
