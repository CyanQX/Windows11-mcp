"""Where desktop work runs.

Every UI Automation call and every piece of synthetic mouse/keyboard input is funnelled through
ONE long-lived worker thread whose COM apartment is initialised exactly once (multithreaded, as
Microsoft recommends for UI Automation clients). This gives two guarantees:

* COM pointers (UIA elements, patterns, the IUIAutomation client) are created and used on the
  same thread, instead of hopping between whatever pool threads ``asyncio.to_thread`` picks;
* two tool calls can never interleave their input -- a Type cannot be split by a concurrent Click.

Tools that do not touch the desktop (PowerShell, FileSystem, Registry, Process, Notification)
keep running on the default executor so a slow command never blocks the UI.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_thread_ident: int | None = None


def _initialise_thread() -> None:
    global _thread_ident
    _thread_ident = threading.get_ident()
    try:
        import comtypes

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError as exc:  # already initialised in another mode -- still usable
        logger.debug("CoInitializeEx on the desktop thread: %s", exc)
    except ImportError:  # non-Windows test environments
        pass


def desktop_executor() -> ThreadPoolExecutor:
    """The single worker thread that owns all desktop (UIA + input) work."""
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="wmcp-desktop",
                initializer=_initialise_thread,
            )
        return _executor


def on_desktop_thread() -> bool:
    return _thread_ident is not None and threading.get_ident() == _thread_ident


async def run_on_desktop(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking desktop function on the desktop thread without blocking the event loop."""
    if on_desktop_thread():
        return func(*args, **kwargs)
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    call = functools.partial(context.run, func, *args, **kwargs)
    return await loop.run_in_executor(desktop_executor(), call)


def call_on_desktop(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Synchronous variant of :func:`run_on_desktop` (safe to call from the desktop thread)."""
    if on_desktop_thread():
        return func(*args, **kwargs)
    return desktop_executor().submit(func, *args, **kwargs).result()
