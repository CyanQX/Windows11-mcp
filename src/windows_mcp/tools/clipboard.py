"""Clipboard tool — copy/paste clipboard operations."""

from functools import partial
from typing import Literal

from fastmcp import Context
from windows_mcp.tools._common import declare_tool


def register(mcp, *, get_desktop, get_analytics):
    tool = partial(declare_tool, mcp, get_analytics)

    @tool(
        "Clipboard",
        description='Copy/paste clipboard operations. Keywords: copy, paste, cut, clipboard, text transfer. Use mode="get" to read current clipboard content, mode="set" to set clipboard text.',
        access="adjust",
        analytics="Clipboard-Tool",
    )
    def clipboard_tool(
        mode: Literal["get", "set"], text: str | None = None, ctx: Context = None,
    ) -> str:
        try:
            import win32clipboard

            if mode == "get":
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                        return f"Clipboard content:\n{data}"
                    else:
                        return "Clipboard is empty or contains non-text data."
                finally:
                    win32clipboard.CloseClipboard()
            elif mode == "set":
                if text is None:
                    return "Error: text parameter required for set mode."
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                    return f"Clipboard set to: {text[:100]}{'...' if len(text) > 100 else ''}"
                finally:
                    win32clipboard.CloseClipboard()
            else:
                return 'Error: mode must be either "get" or "set".'
        except Exception:
            raise
