"""Excess-page cleanup must stay alive until it completes, including shutdown."""
import asyncio
import gc
from types import SimpleNamespace

from windows_mcp.browser.playwright_backend import PlaywrightBackend


async def test_page_limit_keeps_close_task_alive_until_shutdown():
    backend = PlaywrightBackend()
    release = asyncio.Event()
    closed = []

    async def close():
        await release.wait()
        closed.append(True)

    browser = SimpleNamespace(pages={str(i): object() for i in range(16)}, diagnostics=[])
    assert backend._add_page(browser, SimpleNamespace(close=close)) == "page-limit"
    await asyncio.sleep(0)
    gc.collect()
    assert len(backend._background_tasks) == 1 and not closed
    release.set()
    await backend.close_all()
    assert closed == [True] and not backend._background_tasks


async def test_page_close_failure_is_collected_and_reported():
    backend = PlaywrightBackend()

    async def close():
        raise RuntimeError("isolated close failure")

    browser = SimpleNamespace(pages={str(i): object() for i in range(16)}, diagnostics=[])
    backend._add_page(browser, SimpleNamespace(close=close))
    await backend.close_all()
    assert not backend._background_tasks
    assert browser.diagnostics == ["Closing an excess browser page failed"]
