"""Isolated browser contexts, semantic locators and explicit result verification.

No evaluate, arbitrary CDP connection, cookies or user-profile API is exposed to MCP.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from windows_mcp.engine.contracts import ActionResult, Status, Verification, normalize_expected
from windows_mcp.infrastructure.config import CONFIG_DIR
from windows_mcp.infrastructure.security import validate_url
from windows_mcp.infrastructure.network import origin

_CONTROLS = "button,input,select,textarea,a[href],[role],[data-testid],h1,h2,h3,h4"
_METADATA = """el => {
  const tag = el.tagName.toLowerCase();
  const roles = {button:'button', a:'link', textarea:'textbox', select:'combobox',
                 h1:'heading', h2:'heading', h3:'heading', h4:'heading'};
  let role = el.getAttribute('role') || roles[tag] || '';
  if (tag === 'input') role = ({checkbox:'checkbox',radio:'radio',button:'button',submit:'button'})[el.type] || 'textbox';
  const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
  const name = el.getAttribute('aria-label') || labelled || Array.from(el.labels || []).map(l=>l.textContent).join(' ').trim() ||
               ((tag === 'input' && ['button','submit'].includes(el.type)) ? el.value : '') ||
               (tag === 'input' || tag === 'textarea' ? '' : el.textContent || '').trim() || el.getAttribute('title') || '';
  const rect = el.getBoundingClientRect();
  return {role, name:name.slice(0,300), test_id:el.getAttribute('data-testid'),
          password:el.type === 'password', enabled:!el.disabled && el.getAttribute('aria-disabled') !== 'true',
          editable:!el.readOnly && (tag === 'textarea' || tag === 'input' || el.isContentEditable),
          visible:!!(rect.width && rect.height) && getComputedStyle(el).visibility !== 'hidden' && !el.closest('[aria-hidden=true],[hidden]'),
          bounds:{left:Math.round(rect.left),top:Math.round(rect.top),right:Math.round(rect.right),bottom:Math.round(rect.bottom)}};
}"""

_BATCH_METADATA = """(elements, limit) => {
  const metadata = (""" + _METADATA + """);
  const rows = [];
  let scanned = 0;
  for (const element of elements.slice(0, 2000)) {
    scanned++;
    const item = metadata(element);
    if (item.visible && item.role) rows.push(item);
    if (rows.length >= limit) break;
  }
  return {rows, scanned, truncated: scanned < elements.length,
          password_present: elements.some(el => el.type === 'password')};
}"""



@dataclass
class ManagedBrowser:
    context: object
    profile: tempfile.TemporaryDirectory
    pages: dict = field(default_factory=dict)
    epochs: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    mutation_deadline: float = 0.0
    redirects: dict = field(default_factory=dict)


class PlaywrightBackend:
    def __init__(self, *, allow_private: bool = False, profile_root: Path | None = None, network_policy=None) -> None:
        self.allow_private = allow_private
        self.network_policy = network_policy
        self.profile_root = profile_root or CONFIG_DIR / "browser-profiles"
        self._playwright = None
        self._manager = None
        self._sessions: dict[str, ManagedBrowser] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task] = set()

    async def _url(self, url: str, session_id: str) -> None:
        if self.network_policy is None:
            from windows_mcp.engine.sessions import get_runtime

            policy = get_runtime().policy
        else:
            policy = self.network_policy
        policy.check_network(session_id, url)
        await asyncio.wait_for(asyncio.to_thread(validate_url, url, allow_private=self.allow_private), timeout=5)

    def _add_page(self, browser: ManagedBrowser, page) -> str:
        for page_id, known in browser.pages.items():
            if known is page:
                return page_id
        if len(browser.pages) >= 16:
            task = asyncio.create_task(page.close())
            self._background_tasks.add(task)
            def finished(done):
                self._background_tasks.discard(done)
                if not done.cancelled() and done.exception() is not None:
                    browser.diagnostics[:] = ["Closing an excess browser page failed"]
            task.add_done_callback(finished)
            return "page-limit"
        page_id = f"page_{uuid.uuid4().hex[:12]}"
        browser.pages[page_id] = page
        browser.epochs[page_id] = 0

        def navigated(frame):
            browser.epochs[page_id] += 1
            browser.targets = {key: value for key, value in browser.targets.items() if value["page_id"] != page_id}

        async def dismiss(dialog):
            browser.diagnostics[:] = ["A native browser dialog was dismissed; no confirmation was submitted"]
            await dialog.dismiss()

        page.on("framenavigated", navigated)
        page.on("dialog", dismiss)
        return page_id

    async def _start(self, session: str, channel: str, headless: bool) -> dict:
        if session in self._sessions:
            return self._status(session)
        if channel not in {"chromium", "msedge", "chrome"}:
            raise ValueError("channel must be chromium, msedge or chrome")
        if self._playwright is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError("Browser requires `uv sync --extra browser`; install Chromium with `uv run playwright install chromium`") from exc
            self._manager = async_playwright()
            self._playwright = await self._manager.start()
        self.profile_root.mkdir(parents=True, exist_ok=True)
        profile = tempfile.TemporaryDirectory(prefix="session-", dir=self.profile_root)
        try:
            context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=profile.name, channel=None if channel == "chromium" else channel,
                headless=headless, accept_downloads=False, service_workers="block",
            )
            browser = ManagedBrowser(context, profile)

            async def route(request_route):
                response = None
                checked_url = request_route.request.url
                try:
                    await self._url(request_route.request.url, session)
                    if request_route.request.method not in {"GET", "HEAD", "OPTIONS"} and time.monotonic() > browser.mutation_deadline:
                        raise ValueError("Background mutation is not authorized")
                    # Playwright routes only the first request of an automatic redirect chain.
                    # Fetch exactly one response so redirects cannot leave the approved boundary
                    # before a later page.url check. Never issue the original request twice.
                    response = await request_route.fetch(max_redirects=0, max_retries=0, timeout=10000)
                    if 300 <= response.status < 400 and response.headers.get("location"):
                        destination = urljoin(request_route.request.url, response.headers["location"])
                        checked_url = destination
                        await self._url(destination, session)
                        request = request_route.request
                        if request.is_navigation_request() and request.frame == request.frame.page.main_frame and request.method in {"GET", "HEAD"}:
                            browser.redirects[request.frame.page] = destination
                            # Finish this navigation without a Chromium error-page race.
                            # The navigation loop follows the validated URL as a new request.
                            await request_route.fulfill(status=200, content_type="text/html", body="")
                            return
                        browser.diagnostics[:] = ["HTTP redirect was paused before following; top-level GET navigation may continue only after the destination is checked. Redirected subresources/forms are blocked."]
                        await request_route.abort()
                        return
                    await request_route.fulfill(response=response)
                except (ValueError, PermissionError, TimeoutError):
                    try:
                        target_origin = origin(checked_url)
                    except ValueError:
                        target_origin = "invalid HTTP target"
                    browser.diagnostics[:] = [f"HTTP request blocked before dispatch to {target_origin}; check this session's network origin grants and private-address policy"]
                    await request_route.abort()
                except Exception:
                    browser.diagnostics[:] = ["An isolated HTTP request failed or timed out; it was not retried"]
                    try:
                        await request_route.abort()
                    except Exception:
                        pass  # Context may already have been closed by cancellation.
                finally:
                    if response is not None:
                        await response.dispose()

            async def block_socket(socket):
                await socket.close()

            await context.route("**/*", route)
            await context.route_web_socket("**/*", block_socket)
            context.on("page", lambda page: self._add_page(browser, page))
            self._sessions[session] = browser
            for page in context.pages:
                self._add_page(browser, page)
            if not browser.pages:
                self._add_page(browser, await context.new_page())
            return self._status(session)
        except BaseException:
            profile.cleanup()
            raise

    def _status(self, session: str) -> dict:
        browser = self._sessions.get(session)
        return {"status": Status.VERIFIED.value, "source": "playwright", "running": browser is not None,
                "pages": [{"page_id": key, "url": page.url, "closed": page.is_closed()}
                          for key, page in browser.pages.items()] if browser else [],
                "pending_redirects": [{"page_id": key, "url": browser.redirects[page]}
                    for key, page in browser.pages.items() if page in browser.redirects] if browser else [],
                "diagnostics": list(browser.diagnostics) if browser else []}

    def _page(self, session: str, page_id: str | None):
        browser = self._sessions.get(session)
        if browser is None:
            raise RuntimeError("Browser is not started; use Browser(operation='start')")
        active = {key: page for key, page in browser.pages.items() if not page.is_closed()}
        if page_id is None:
            if len(active) != 1:
                raise ValueError("AMBIGUOUS_TARGET: choose a page_id from Browser(status)")
            page_id = next(iter(active))
        if page_id not in active:
            raise ValueError("TARGET_STALE: managed page was closed or belongs to another client")
        return browser, page_id, active[page_id]

    async def observe(self, session: str, page_id: str | None, *, limit: int = 100) -> dict:
        # Reading is safely retryable: an iframe may finish navigation during the first snapshot.
        for attempt in range(3):
            result = await self._observe_once(session, page_id, limit=limit)
            browser, observed_page_id, _ = self._page(session, result["context"]["page_id"])
            if result["revision"] == browser.epochs[observed_page_id]:
                if attempt:
                    result["diagnostics"].append("Observation refreshed after frame navigation")
                return result
            await asyncio.sleep(0)
        raise RuntimeError("TARGET_STALE: page kept navigating while observed; wait and observe again")

    async def _observe_once(self, session: str, page_id: str | None, *, limit: int = 100) -> dict:
        if not 1 <= limit <= 300:
            raise ValueError("limit must be 1..300")
        browser, page_id, page = self._page(session, page_id)
        epoch = browser.epochs[page_id]
        browser.targets = {key: value for key, value in browser.targets.items() if value["page_id"] != page_id}
        nodes, frames, truncated = [], [], len(page.frames) > 20
        remaining, password_present, metadata_calls = limit, False, 0
        for frame_index, frame in enumerate(page.frames[:20]):
            frame_id = f"frame_{frame_index}"
            frames.append({"frame_id": frame_id, "url": frame.url})
            batch = await frame.locator(_CONTROLS).evaluate_all(_BATCH_METADATA, remaining)
            metadata_calls += 1
            truncated |= batch["truncated"]
            password_present |= batch["password_present"]
            for metadata in batch["rows"]:
                target = f"playwright:{session}:{page_id}:{epoch}:{uuid.uuid4().hex[:10]}"
                spec = {"page_id": page_id, "frame": frame, "frame_id": frame_id, "epoch": epoch, **metadata}
                browser.targets[target] = spec
                actions = ["click"] if metadata["enabled"] else []
                if metadata["enabled"]:
                    if metadata["editable"]:
                        actions.append("fill")
                    if metadata["role"] in {"checkbox", "radio"}:
                        actions.append("check")
                    if metadata["role"] == "combobox":
                        actions.append("select")
                nodes.append({"element_id": target, "source": "playwright", "role": metadata["role"],
                              "name": metadata["name"], "enabled": metadata["enabled"], "visible": True,
                              "bounds": metadata["bounds"], "state": {"sensitive": metadata["password"]},
                              "actions": actions, "frame_id": frame_id})
            remaining = limit - len(nodes)
            if remaining <= 0:
                truncated |= frame_index + 1 < len(page.frames)
                break
        diagnostics = list(browser.diagnostics)
        # Avoid returning password values even if a provider exposes them in an aria snapshot.
        aria = ""
        if password_present or len(frames) < len(page.frames):
            diagnostics.append("Password field present; raw aria snapshot omitted")
        else:
            aria = await page.locator("body").aria_snapshot(timeout=3000)
            if len(aria) > 12000:
                aria, truncated = aria[:12000], True
        return {"observation_id": f"obs_{uuid.uuid4().hex}", "session_id": session, "revision": epoch,
                "context": {"source": "playwright", "page_id": page_id, "url": page.url,
                            "coordinate_space": "css_pixels", "frames": frames, "metadata_calls": metadata_calls},
                "elements": nodes, "aria_snapshot": aria, "truncated": truncated, "diagnostics": diagnostics}

    async def _action(self, session: str, operation: str, *, page_id: str | None, target: str | None,
                      value: str | None, expected: dict | None, timeout_ms: int) -> dict:
        browser, page_id, page = self._page(session, page_id)
        result = ActionResult(status=Status.FAILED, target=target, method=f"playwright_{operation}")
        spec = browser.targets.get(target)
        if spec is None or spec["page_id"] != page_id or spec["epoch"] != browser.epochs[page_id]:
            return ActionResult(status=Status.TARGET_STALE, target=target, retryable=True,
                                errors=["Observe the page again after navigation or page closure"]).model_dump()
        frame = spec["frame"]
        locator = frame.get_by_test_id(spec["test_id"]) if spec["test_id"] else frame.get_by_role(spec["role"], name=spec["name"], exact=True)
        count = await locator.count()
        if count != 1:
            result.status = Status.AMBIGUOUS_TARGET if count > 1 else Status.TARGET_STALE
            result.errors = [f"Locator matched {count} controls; observe/refine the target"]
            return result.model_dump()
        if expected is not None and expected.get("kind") not in {"url", "text", "value", "checked"}:
            raise ValueError("Browser expected.kind must be url, text, value or checked")
        from windows_mcp.infrastructure.authorization import check_dispatch

        check_dispatch()
        result.precondition_passed = True
        result.input_dispatched = True
        browser.mutation_deadline = time.monotonic() + timeout_ms / 1000
        if operation == "click":
            await locator.click(timeout=timeout_ms)
        elif operation == "fill":
            if value is None:
                raise ValueError("fill requires value")
            await locator.fill(value, timeout=timeout_ms)
        elif operation == "check":
            if value not in {"true", "false"}:
                raise ValueError("check requires value='true' or 'false'")
            await locator.set_checked(value == "true", timeout=timeout_ms)
        elif operation == "select":
            if value is None:
                raise ValueError("select requires value")
            await locator.select_option(value=value, timeout=timeout_ms)
        else:
            raise ValueError("Unsupported browser action")
        passed, evidence = None, "none"
        if expected:
            kind = expected["kind"]
            if kind == "url":
                passed = page.url == expected.get("value")
            elif kind == "text":
                passed = await frame.get_by_text(expected.get("value", ""), exact=True).count() == 1
            elif kind == "value" and not spec["password"]:
                passed = await locator.input_value(timeout=timeout_ms) == expected.get("value")
            elif kind == "checked":
                passed = await locator.is_checked(timeout=timeout_ms) == expected.get("value")
            evidence = f"browser_{kind}"
        elif operation in {"fill", "select"} and not spec["password"]:
            passed, evidence = await locator.input_value(timeout=timeout_ms) == value, "browser_field_value"
        elif operation == "check":
            passed, evidence = await locator.is_checked(timeout=timeout_ms) == (value == "true"), "browser_checked"
        result.status = Status.VERIFIED if passed is True else Status.DISPATCHED_UNVERIFIED
        result.verification = Verification(passed=passed, evidence_type=evidence, detail="Postcondition matched" if passed else "Result must be independently verified before repeating")
        result.new_revision = browser.epochs[page_id]
        browser.mutation_deadline = 0
        return result.model_dump()

    async def run(self, session_id: str, operation: str, *, page_id: str | None = None,
                  target: str | None = None, url: str | None = None, value: str | None = None,
                  expected: dict | None = None, limit: int = 100, timeout_ms: int = 10000,
                  channel: str = "msedge", headless: bool = False) -> dict:
        expected = normalize_expected(expected, browser=True)
        if not 100 <= timeout_ms <= 30000:
            raise ValueError("timeout_ms must be 100..30000")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            from windows_mcp.infrastructure.authorization import check_dispatch

            check_dispatch()  # A cancelled call must not revive after waiting for this lock.
            if operation == "start":
                return await self._start(session_id, channel, headless)
            if operation == "status":
                return self._status(session_id)
            if operation == "close":
                await self.close_session(session_id)
                return self._status(session_id)
            if operation == "observe":
                return await asyncio.wait_for(self.observe(session_id, page_id, limit=limit), timeout=timeout_ms / 1000)
            if operation == "new_page":
                browser, _, _ = self._page(session_id, page_id)
                page = await browser.context.new_page()
                page_id = self._add_page(browser, page)
                if url is None:
                    return self._status(session_id)
                operation = "navigate"
            if operation == "navigate":
                if not url:
                    raise ValueError("navigate requires url")
                await self._url(url, session_id)
                browser, page_id, page = self._page(session_id, page_id)
                deadline = time.monotonic() + timeout_ms / 1000
                for _ in range(6):
                    browser.redirects.pop(page, None)
                    await self._url(url, session_id)
                    try:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Navigation deadline exceeded")
                        await page.goto(url, wait_until="domcontentloaded", timeout=max(1, int(remaining * 1000)))
                    except Exception:
                        destination = browser.redirects.pop(page, None)
                        if destination is None:
                            raise
                        url = destination
                        continue
                    destination = browser.redirects.pop(page, None)
                    if destination is not None:
                        url = destination
                        continue
                    break
                else:
                    raise ValueError("Too many HTTP redirects; no further request was sent")
                # Redirections are checked by the context-wide route as well as at navigation time.
                await self._url(page.url, session_id)
                return {"status": Status.VERIFIED.value, "source": "playwright", "page_id": page_id,
                        "url": page.url, "title": (await page.title())[:300],
                        "verification": {"passed": True, "evidence_type": "navigation_url", "detail": "Loaded URL was read back"}}
            if operation == "read":
                _, _, page = self._page(session_id, page_id)
                text = await page.locator("body").inner_text(timeout=timeout_ms)
                return {"source": "playwright", "text": text[:12000], "truncated": len(text) > 12000}
            try:
                return await self._action(session_id, operation, page_id=page_id, target=target, value=value,
                                          expected=expected, timeout_ms=timeout_ms)
            except Exception as exc:
                status = Status.TIMEOUT if type(exc).__name__ == "TimeoutError" else Status.FAILED
                return ActionResult(status=status, target=target, method=f"playwright_{operation}",
                                    input_dispatched=True, errors=[f"{type(exc).__name__}: browser action did not complete; read state before retrying"]).model_dump()
            finally:
                browser = self._sessions.get(session_id)
                if browser:
                    browser.mutation_deadline = 0

    async def close_session(self, session_id: str) -> None:
        browser = self._sessions.pop(session_id, None)
        if browser:
            try:
                await browser.context.close()
            finally:
                browser.profile.cleanup()

    async def close_all(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        for session in list(self._sessions):
            await self.close_session(session)
        if self._manager:
            await self._manager.__aexit__(None, None, None)
        self._playwright, self._manager = None, None
        self._locks.clear()
