"""Snapshot and Screenshot tools — desktop state capture."""

import logging

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context

from windows_mcp.tools._snapshot_helpers import (
    _as_bool,
    capture_desktop_state,
    build_snapshot_response,
)

logger = logging.getLogger(__name__)

# Populated by register(); exposed for backward-compatible test imports.
state_tool = None
screenshot_tool = None


def register(mcp, *, get_desktop, get_analytics):
    global state_tool, screenshot_tool
    @mcp.tool(
        name='Snapshot',
        description="Inspect the live UI of real windows. Keywords: screenshot, screen capture, see screen, observe, look, inspect, UI elements, what's on screen. Captures focused/opened windows, interactive elements (buttons, text fields, links, menus with coordinates) and scrollable areas; every element carries a #N label usable as label= in Click/Type/Scroll/Move or target= in Act (labels are re-located live before use, so they survive window moves). Set window='title', a process name like 'notepad', or a handle to capture only that window (much faster, recommended). Set use_vision=True to include a screenshot with cursor highlight; use_annotation=False for a clean screenshot without boxes. Set use_ui_tree=False for a screenshot-only capture. Set width_reference_line/height_reference_line to overlay a grid (vision only). Set use_dom=True for browser page content instead of browser UI. Set use_words=True to also list every word of text fields/documents as clickable 'word' nodes (off by default: in editors it floods the element budget). Set display=[0] or [0,1] to limit to zero-based displays. For a quick targeted lookup of one control prefer Find.",
        annotations=ToolAnnotations(
            title="Snapshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "State-Tool")
    def _state_tool(
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        use_annotation: bool | str = True,
        use_ui_tree: bool | str = True,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        window: str | None = None,
        use_words: bool | str = False,
        ctx: Context = None,
    ):
        window_handles = None
        if window is not None and str(window).strip():
            from windows_mcp.desktop import elements

            matches = elements.resolve_windows(window)
            if not matches:
                raise ValueError(
                    f"No open window matches {window!r}. Open windows: {elements.describe_windows()}"
                )
            window_handles = [match.handle for match in matches[:3]]
        try:
            capture_result = capture_desktop_state(
                get_desktop(),
                use_vision=_as_bool(use_vision),
                use_dom=_as_bool(use_dom),
                use_annotation=_as_bool(use_annotation),
                use_ui_tree=_as_bool(use_ui_tree),
                width_reference_line=width_reference_line,
                height_reference_line=height_reference_line,
                display=display,
                tool_name="Snapshot tool",
                window_handles=window_handles,
                use_words=_as_bool(use_words),
            )
        except Exception as e:
            logger.warning(
                "Snapshot failed with display=%s use_vision=%s use_dom=%s",
                display,
                use_vision if 'use_vision' in locals() else None,
                use_dom if 'use_dom' in locals() else None,
                exc_info=True,
            )
            # Raise so the client sees isError=true instead of a "successful" error string.
            raise RuntimeError(f'Error capturing desktop state: {str(e)}. Please try again.') from e

        return build_snapshot_response(capture_result, include_ui_details=True)

    @mcp.tool(
        name='Screenshot',
        description="Captures a fast screenshot-first desktop snapshot with cursor position, desktop/window summaries, and an image. This path skips UI tree extraction for speed. Use Snapshot when you need interactive element ids, scrollable regions, or browser DOM extraction. Note: the returned image may be downscaled for efficiency; when it is, multiply image coordinates by the ratio of original size to displayed size to get the actual screen coordinates for mouse actions (Click, Move, etc.).",
        annotations=ToolAnnotations(
            title="Screenshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Screenshot-Tool")
    def _screenshot_tool(
        use_annotation: bool | str = False,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        ctx: Context = None,
    ):
        try:
            capture_result = capture_desktop_state(
                get_desktop(),
                use_vision=True,
                use_dom=False,
                use_annotation=_as_bool(use_annotation),
                use_ui_tree=False,
                width_reference_line=width_reference_line,
                height_reference_line=height_reference_line,
                display=display,
                tool_name="Screenshot tool",
            )
        except Exception as e:
            logger.warning(
                "Screenshot failed with display=%s",
                display,
                exc_info=True,
            )
            raise RuntimeError(f'Error capturing screenshot: {str(e)}. Please try again.') from e

        return build_snapshot_response(
            capture_result,
            include_ui_details=False,
            ui_detail_note="UI Tree: Skipped for fast screenshot-only capture. Call Snapshot when you need interactive or scrollable elements.",
        )

    state_tool = _state_tool
    screenshot_tool = _screenshot_tool
