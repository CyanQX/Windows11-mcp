from windows_mcp.desktop.utils import (
    resolve_known_folder_guid_path,
)
from windows_mcp.powershell import PowerShellExecutor
from windows_mcp.vdm.core import (
    get_all_desktops,
    get_current_desktop,
    is_window_on_current_desktop,
)
from windows_mcp.desktop.views import DesktopState, Window, Browser, Status, Size, Display
from windows_mcp.tree.views import BoundingBox, TreeElementNode, TreeState, SemanticNode
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from PIL import ImageFont, ImageDraw, Image
from windows_mcp.tree.service import Tree
from windows_mcp.desktop import screenshot as screenshot_capture
from windows_mcp.desktop import flash_overlay
from windows_mcp.desktop import native_input
from windows_mcp.desktop import elements
from windows_mcp.infrastructure import validate_url
from urllib.parse import urljoin
from locale import getpreferredencoding
from typing import Literal
from markdownify import markdownify
from thefuzz import process
from time import sleep, time, perf_counter, monotonic
from psutil import Process
import math
import win32process
import win32gui
import win32con
import requests
import logging
import random
import ctypes
import csv
import os
import io

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

import windows_mcp.uia as uia  # noqa: E402


def _snapshot_profile_enabled() -> bool:
    value = os.getenv("WINDOWS_MCP_PROFILE_SNAPSHOT", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Desktop:
    def __init__(self):
        self.encoding = getpreferredencoding()
        self.tree = Tree(self)
        self.desktop_state = None
        self._element_registry = elements.ElementRegistry()
        self._apps_cache: tuple[float, dict[str, str]] | None = None

    @property
    def element_registry(self) -> "elements.ElementRegistry":
        # Created lazily as well, because tests build Desktop via __new__ without __init__.
        registry = getattr(self, "_element_registry", None)
        if registry is None:
            registry = self._element_registry = elements.ElementRegistry()
        return registry

    def label_node(self, label: int):
        """The Snapshot node behind a label (interactive nodes first, then scrollable ones)."""
        if self.desktop_state is None or self.desktop_state.tree_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        tree_state = self.desktop_state.tree_state
        if 0 <= label < len(tree_state.interactive_nodes):
            return tree_state.interactive_nodes[label]
        scroll_idx = label - len(tree_state.interactive_nodes)
        if 0 <= scroll_idx < len(tree_state.scrollable_nodes):
            return tree_state.scrollable_nodes[scroll_idx]
        raise IndexError(f"Label {label} out of range")

    def locate_label(self, label: int) -> "elements.Element | None":
        """Resolve a Snapshot label to the live element it points at *now*.

        Returns ``None`` for nodes without their own UI element (word boxes), in which case the
        recorded coordinates are the best information available. Raises when the element is gone.
        """
        return elements.resolve_snapshot_node(self.label_node(label))

    def get_state(
        self,
        use_annotation: bool | str = True,
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        use_ui_tree: bool | str = True,
        as_bytes: bool | str = False,
        scale: float = 1.0,
        grid_lines: tuple[int, int] | None = None,
        display_indices: list[int] | None = None,
        max_image_size: Size | None = None,
        window_handles: list[int] | None = None,
        use_words: bool = False,
    ) -> DesktopState:
        use_annotation = use_annotation is True or (
            isinstance(use_annotation, str) and use_annotation.lower() == "true"
        )
        use_vision = use_vision is True or (
            isinstance(use_vision, str) and use_vision.lower() == "true"
        )
        use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
        use_ui_tree = use_ui_tree is True or (
            isinstance(use_ui_tree, str) and use_ui_tree.lower() == "true"
        )
        as_bytes = as_bytes is True or (isinstance(as_bytes, str) and as_bytes.lower() == "true")

        if use_dom and not use_ui_tree:
            raise ValueError("use_dom=True requires use_ui_tree=True")

        start_time = time()
        profile_enabled = _snapshot_profile_enabled()
        profile_started_at = perf_counter()
        stage_started_at = profile_started_at
        desktop_context_ms = 0.0
        tree_capture_ms = 0.0
        region_filter_ms = 0.0
        screenshot_capture_ms = 0.0
        screenshot_resize_ms = 0.0
        state_build_ms = 0.0
        displays = self.get_displays()
        available_displays = [self._display_to_view(display) for display in displays]
        capture_rect = (
            self.get_display_union_rect(display_indices, displays) if display_indices else None
        )
        screenshot_region = self._rect_to_bounding_box(capture_rect) if capture_rect else None

        # Fast path for Screenshot tool (use_ui_tree=False): skip window enumeration.
        # UIAutomation calls (get_controls_handles / get_windows / get_active_window)
        # can hang when an app is launching and not responding to WM messages.
        if use_ui_tree:
            controls_handles = self.get_controls_handles()  # Taskbar,Program Manager,Apps, Dialogs
            windows, windows_handles = self.get_windows(controls_handles=controls_handles)  # Apps
            active_window = self.get_active_window(windows=windows)  # Active Window
            active_window_handle = active_window.handle if active_window else None
        else:
            controls_handles = set()
            windows = []
            windows_handles = set()
            active_window = None
            active_window_handle = None

        cursor_position = self.get_cursor_location()

        try:
            active_desktop = get_current_desktop()
            all_desktops = get_all_desktops()
        except RuntimeError:
            active_desktop = {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "Default Desktop",
            }
            all_desktops = [active_desktop]

        if active_window is not None and active_window in windows:
            windows.remove(active_window)

        if profile_enabled:
            desktop_context_ms = (perf_counter() - stage_started_at) * 1000
            stage_started_at = perf_counter()

        logger.debug(f"Active window: {active_window or 'No Active Window Found'}")
        logger.debug(f"Windows: {windows}")

        if use_ui_tree:
            other_windows_handles = set(controls_handles - windows_handles)
            if active_window_handle is not None:
                other_windows_handles.discard(active_window_handle)
            tree_active_window_handle = active_window_handle
            if screenshot_region:
                active_window_in_region = (
                    self._filter_window_to_region(active_window, screenshot_region) is not None
                )
                tree_active_window_handle = (
                    active_window_handle if active_window_in_region else None
                )
                other_windows_handles.update(
                    window.handle
                    for window in windows
                    if self._filter_window_to_region(window, screenshot_region) is not None
                )
            if window_handles:
                # Scoped capture: only walk the requested window(s) -- much faster than the
                # whole desktop and keeps the element budget for the window that matters.
                wanted = set(window_handles)
                tree_active_window_handle = (
                    active_window_handle if active_window_handle in wanted else None
                )
                other_windows_handles = wanted - {active_window_handle}
            tree_state = self.tree.get_state(
                tree_active_window_handle,
                list(other_windows_handles),
                use_dom=use_dom,
                include_words=use_words,
            )
        else:
            root_box = screenshot_region or self.tree.screen_box
            tree_state = TreeState(
                status=True,
                root_node=TreeElementNode(
                    name="Desktop",
                    control_type="PaneControl",
                    bounding_box=root_box,
                    center=root_box.get_center(),
                    window_name="Desktop",
                    metadata={},
                ),
            )

        if profile_enabled:
            tree_capture_ms = (perf_counter() - stage_started_at) * 1000
            stage_started_at = perf_counter()

        if screenshot_region:
            active_window = self._filter_window_to_region(active_window, screenshot_region)
            windows = self._filter_windows_to_region(windows, screenshot_region)
            if use_ui_tree:
                tree_state = self._filter_tree_state_to_region(tree_state, screenshot_region)
            if cursor_position and not self._point_in_region(cursor_position, screenshot_region):
                cursor_position = None

        if profile_enabled:
            region_filter_ms = (perf_counter() - stage_started_at) * 1000
            stage_started_at = perf_counter()

        screenshot_original_size = None
        applied_scale = None
        if use_vision:
            if use_annotation:
                nodes = tree_state.interactive_nodes
                screenshot = self.get_annotated_screenshot(
                    nodes=nodes,
                    cursor_pos=cursor_position,
                    grid_lines=grid_lines,
                    capture_rect=capture_rect,
                )
            else:
                screenshot = self.get_screenshot(capture_rect=capture_rect)

            screenshot_original_size = Size(width=screenshot.width, height=screenshot.height)

            if profile_enabled:
                screenshot_capture_ms = (perf_counter() - stage_started_at) * 1000
                stage_started_at = perf_counter()

            if max_image_size:
                scale_width = (
                    max_image_size.width / screenshot.width
                    if screenshot.width > max_image_size.width
                    else 1.0
                )
                scale_height = (
                    max_image_size.height / screenshot.height
                    if screenshot.height > max_image_size.height
                    else 1.0
                )
                scale = min(scale, scale_width, scale_height)

            applied_scale = scale
            if scale != 1.0:
                screenshot = screenshot.resize(
                    (int(screenshot.width * scale), int(screenshot.height * scale)),
                    Image.LANCZOS,
                )

            if profile_enabled:
                screenshot_resize_ms = (perf_counter() - stage_started_at) * 1000
                stage_started_at = perf_counter()

            if as_bytes:
                buffered = io.BytesIO()
                screenshot.save(buffered, format="PNG", optimize=True, compress_level=6)
                screenshot = buffered.getvalue()
                buffered.close()
        else:
            screenshot = None

        self.desktop_state = DesktopState(
            active_window=active_window,
            windows=windows,
            active_desktop=active_desktop,
            all_desktops=all_desktops,
            screenshot=screenshot,
            cursor_position=cursor_position,
            screenshot_original_size=screenshot_original_size,
            screenshot_scale=applied_scale,
            screenshot_region=screenshot_region,
            screenshot_displays=display_indices,
            available_displays=available_displays,
            tree_state=tree_state,
            screenshot_backend=getattr(self, "_last_screenshot_backend", None)
            if use_vision
            else None,
            capture_sec=time() - start_time,
        )
        if profile_enabled:
            state_build_ms = (perf_counter() - stage_started_at) * 1000
            total_profile_ms = (perf_counter() - profile_started_at) * 1000
            logger.info(
                "Snapshot profile: desktop_context_ms=%.1f tree_capture_ms=%.1f region_filter_ms=%.1f screenshot_capture_ms=%.1f screenshot_resize_ms=%.1f state_build_ms=%.1f total_ms=%.1f use_vision=%s use_dom=%s use_ui_tree=%s use_annotation=%s displays=%s",
                desktop_context_ms,
                tree_capture_ms,
                region_filter_ms,
                screenshot_capture_ms,
                screenshot_resize_ms,
                state_build_ms,
                total_profile_ms,
                use_vision,
                use_dom,
                use_ui_tree,
                use_annotation,
                display_indices,
            )
        # Log the time taken to capture the state
        end_time = time()
        logger.info(f"Desktop State capture took {end_time - start_time:.2f} seconds")
        return self.desktop_state

    def get_window_status(self, control: uia.Control) -> Status:
        if uia.IsIconic(control.NativeWindowHandle):
            return Status.MINIMIZED
        elif uia.IsZoomed(control.NativeWindowHandle):
            return Status.MAXIMIZED
        elif uia.IsWindowVisible(control.NativeWindowHandle):
            return Status.NORMAL
        else:
            return Status.HIDDEN

    def get_cursor_location(self) -> tuple[int, int]:
        return uia.GetCursorPos()

    _APPS_CACHE_SECONDS = 300.0

    def get_apps_from_start_menu(self) -> dict[str, str]:
        """Installed apps (name -> AppID/path), cached for a few minutes.

        ``Get-StartApps`` costs a PowerShell start-up (~1 s) -- far too slow to repeat on every
        launch.
        """
        cache = getattr(self, "_apps_cache", None)
        if cache and monotonic() - cache[0] < self._APPS_CACHE_SECONDS:
            return cache[1]
        apps = self._load_apps_from_start_menu()
        if apps:
            self._apps_cache = (monotonic(), apps)
        return apps

    def _load_apps_from_start_menu(self) -> dict[str, str]:
        """Get installed apps. Tries Get-StartApps first, falls back to shortcut scanning."""
        command = "Get-StartApps | ConvertTo-Csv -NoTypeInformation"
        apps_info, status = PowerShellExecutor.execute_command(command)

        if status == 0 and apps_info and apps_info.strip():
            try:
                reader = csv.DictReader(io.StringIO(apps_info.strip()))
                apps = {
                    row.get("Name", "").lower(): row.get("AppID", "")
                    for row in reader
                    if row.get("Name") and row.get("AppID")
                }
                if apps:
                    return apps
            except Exception as e:
                logger.warning(f"Error parsing Get-StartApps output: {e}")

        # Fallback: scan Start Menu shortcut folders (works on all Windows versions)
        logger.info("Get-StartApps unavailable, falling back to Start Menu folder scan")
        return self._get_apps_from_shortcuts()

    def _get_apps_from_shortcuts(self) -> dict[str, str]:
        """Scan Start Menu folders for .lnk shortcuts as a fallback for Get-StartApps."""
        import glob

        apps = {}
        start_menu_paths = [
            os.path.join(
                os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
            os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
        ]
        for base_path in start_menu_paths:
            if not os.path.isdir(base_path):
                continue
            for lnk_path in glob.glob(os.path.join(base_path, "**", "*.lnk"), recursive=True):
                name = os.path.splitext(os.path.basename(lnk_path))[0].lower()
                if name and name not in apps:
                    apps[name] = lnk_path
        return apps

    def execute_command(
        self, command: str, timeout: int = 10, shell: str | None = None
    ) -> tuple[str, int]:
        return PowerShellExecutor.execute_command(command, timeout, shell)

    def is_window_browser(self, node: uia.Control):
        """Give any node of the app and it will return True if the app is a browser, False otherwise."""
        try:
            process = Process(node.ProcessId)
            return Browser.has_process(process.name())
        except Exception:
            return False

    def _find_window_by_name(
        self, name: str, refresh_state: bool = False
    ) -> tuple["Window | None", str]:
        """Find a window by fuzzy name match. Returns (window, error_msg).
        If the returned window is None, error_msg describes the failure reason.

        If refresh_state is True, always refresh desktop_state before searching;
        otherwise refresh only when desktop_state is absent or empty.
        """
        if refresh_state or self.desktop_state is None or not self.desktop_state.windows:
            self.get_state()
        if self.desktop_state is None:
            return None, "Failed to get desktop state. Please try again."

        window_list = [
            w
            for w in [self.desktop_state.active_window] + (self.desktop_state.windows or [])
            if w is not None
        ]
        if not window_list:
            return None, "No windows found on the desktop."

        windows = {window.name: window for window in window_list}
        matched_window = process.extractOne(name, list(windows.keys()), score_cutoff=70)
        if matched_window is None:
            return None, f"Application {name.title()} not found."
        window_name, _ = matched_window
        return windows.get(window_name), ""

    def resize_app(
        self, name: str | None = None, size: tuple[int, int] = None, loc: tuple[int, int] = None
    ) -> tuple[str, int]:
        if name is not None:
            target_window, error = self._find_window_by_name(name, refresh_state=True)
            if target_window is None:
                return error, 1
        else:
            # If no name provided, try to resize the active window
            target_window = self.desktop_state.active_window if self.desktop_state else None

            if target_window is None:
                return "No active window found", 1

        # target_window is guaranteed to be non-None here
        if target_window.status == Status.MINIMIZED:
            return f"{target_window.name} is minimized", 1
        elif target_window.status == Status.MAXIMIZED:
            return f"{target_window.name} is maximized", 1
        else:
            window_control = uia.ControlFromHandle(target_window.handle)
            if loc is None:
                x = window_control.BoundingRectangle.left
                y = window_control.BoundingRectangle.top
                loc = (x, y)
            if size is None:
                width = window_control.BoundingRectangle.width()
                height = window_control.BoundingRectangle.height()
                size = (width, height)
            x, y = loc
            width, height = size
            window_control.MoveWindow(x, y, width, height)
            return (f"{target_window.name} resized to {width}x{height} at {x},{y}.", 0)

    def app(
        self,
        mode: Literal["launch", "switch", "resize"],
        name: str | None = None,
        loc: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
        handle: int | None = None,
    ):
        match mode:
            case "launch":
                before = {w.handle for w in elements.top_windows()}
                foreground_before = elements.foreground_root()
                response, status, pid = self.launch_app(name)
                if status != 0:
                    return response
                window, reused = self._wait_for_app_window(before, foreground_before, pid, name)
                if window is not None:
                    where = "an existing window came to the front" if reused else "new window"
                    return (
                        f'{name.title()} launched -- {where}: "{window.title}" '
                        f"(handle 0x{window.handle:X}, {elements.process_name(window.pid)})."
                    )
                return (
                    f"Launch of {name.title()} was requested ({response}), but no window appeared "
                    f"within {self._LAUNCH_WAIT_SECONDS:.0f}s. It may still be starting, run in the "
                    "background/tray, or have been blocked -- check with Screenshot."
                )
            case "resize":
                if handle is not None:
                    info = elements.window_info(handle)
                    if info is None:
                        return f"No window with handle {handle}."
                    name = info.title
                response, status = self.resize_app(name=name, size=size, loc=loc)
                return response
            case "switch":
                response, status = self.switch_app(name, handle=handle)
                return response

    _LAUNCH_WAIT_SECONDS = 15.0

    def _wait_for_app_window(
        self, before: set[int], foreground_before: int, pid: int, name: str
    ) -> tuple["elements.TopWindow | None", bool]:
        """Wait for the window a launch produced: new window of the process, else by name."""
        wanted = name.lower().removesuffix(".exe")
        deadline = monotonic() + self._LAUNCH_WAIT_SECONDS
        while monotonic() < deadline:
            windows = elements.top_windows()
            new = [w for w in windows if w.handle not in before]
            by_pid = [w for w in new if pid and w.pid == pid]
            by_name = [
                w
                for w in new
                if wanted in w.title.lower() or wanted in elements.process_name(w.pid).lower()
            ]
            if by_pid or by_name:
                return (by_pid or by_name)[0], False
            foreground = elements.foreground_root()
            if foreground and foreground != foreground_before:
                info = elements.window_info(foreground)
                if info and (
                    wanted in info.title.lower() or wanted in elements.process_name(info.pid).lower()
                ):
                    return info, True  # single-instance app reused its window
            if new and monotonic() > deadline - self._LAUNCH_WAIT_SECONDS + 3:
                return new[0], False  # something new appeared; the title just does not match
            sleep(0.2)
        return None, False

    @staticmethod
    def _shell_execute(target: str) -> tuple[str, int, int]:
        """Launch through ShellExecuteEx on an STA helper thread (shell extensions need STA)."""
        import threading

        from win32com.shell import shell, shellcon

        box: dict[str, object] = {}

        def run() -> None:
            import pythoncom

            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            try:
                info = shell.ShellExecuteEx(
                    fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_NO_UI,
                    lpVerb="open",
                    lpFile=target,
                    nShow=win32con.SW_SHOWNORMAL,
                )
                process_handle = info.get("hProcess")
                if process_handle:
                    box["pid"] = win32process.GetProcessId(process_handle)
                    process_handle.Close()
            except Exception as exc:  # noqa: BLE001 -- reported to the caller
                box["error"] = exc
            finally:
                pythoncom.CoUninitialize()

        thread = threading.Thread(target=run, name="wmcp-launch", daemon=True)
        thread.start()
        thread.join(30)
        if thread.is_alive():
            return (f"launch of {target} is still pending", 0, 0)
        if "error" in box:
            error = box["error"]
            message = getattr(error, "strerror", None) or str(error)
            return (f"Failed to launch {target}: {message}", 1, 0)
        return (f"started {target}", 0, int(box.get("pid", 0) or 0))

    def launch_app(self, name: str) -> tuple[str, int, int]:
        apps_map = self.get_apps_from_start_menu()
        appid = apps_map.get(name.lower())
        if appid is None:
            matched_app = process.extractOne(name, apps_map.keys(), score_cutoff=70)
            appid = apps_map.get(matched_app[0]) if matched_app else None
        if appid is None:
            # Not in the Start menu: fall back to executables on PATH (notepad, calc, mspaint...).
            import shutil

            exe = shutil.which(name) or shutil.which(f"{name}.exe")
            if exe:
                return self._shell_execute(exe)
            return (f"{name.title()} not found in the Start menu or on PATH.", 1, 0)
        if os.path.exists(appid) or "\\" in appid:
            return self._shell_execute(resolve_known_folder_guid_path(appid))
        return self._shell_execute(f"shell:AppsFolder\\{appid}")

    def switch_app(self, name: str | None, handle: int | None = None):
        try:
            if handle is not None:
                info = elements.window_info(handle)
                if info is None:
                    return f"No window with handle {handle}.", 1
                target_handle, title = info.handle, info.title
            else:
                window, error = self._find_window_by_name(name)
                if window is None:
                    return error, 1
                target_handle, title = window.handle, window.name

            was_minimized = uia.IsIconic(target_handle)
            self.bring_window_to_top(target_handle)
            sleep(0.1)
            if elements.foreground_root() != elements.root_of(target_handle):
                current = elements.window_info(elements.foreground_root())
                return (
                    f'Asked Windows to switch to "{title}", but "{current.title if current else "?"}" '
                    "is still in the foreground (Windows can refuse focus changes). Click the window "
                    "instead.",
                    1,
                )
            if was_minimized:
                content = f'Restored "{title}" from minimized and switched to it (verified foreground).'
            else:
                content = f'Switched to "{title}" (verified foreground).'
            return content, 0
        except Exception as e:
            return (f"Error switching app: {str(e)}", 1)

    def window_command(
        self,
        mode: Literal["minimize", "maximize", "restore", "close"],
        name: str | None = None,
        handle: int | None = None,
    ) -> str:
        """Minimize / maximize / restore / close a real top-level window and verify the result."""
        if handle is not None:
            info = elements.window_info(handle)
        else:
            candidates = elements.resolve_windows(name)
            info = candidates[0] if candidates else None
        if info is None:
            return f"No window matches {name or handle!r}. Open windows: {elements.describe_windows()}"
        hwnd = info.handle
        match mode:
            case "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                sleep(0.2)
                ok = bool(win32gui.IsIconic(hwnd))
            case "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                sleep(0.2)
                ok = bool(uia.IsZoomed(hwnd))
            case "restore":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                self.bring_window_to_top(hwnd)
                sleep(0.2)
                ok = not win32gui.IsIconic(hwnd) and not uia.IsZoomed(hwnd)
            case "close":
                # WM_CLOSE is the polite request a click on [X] makes: apps may ask to save.
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                deadline = monotonic() + 3.0
                while monotonic() < deadline and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                    sleep(0.1)
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    return f'Closed "{info.title}" (verified: the window is gone).'
                front = elements.window_info(elements.foreground_root())
                return (
                    f'Asked "{info.title}" to close, but it is still open -- it is probably asking '
                    f'to save changes (foreground: "{front.title if front else "?"}"). Look at it '
                    "before answering."
                )
            case _:
                raise ValueError(f"unsupported window command {mode!r}")
        state = "verified" if ok else "NOT confirmed -- check with Screenshot"
        return f'{mode.title()}d "{info.title}" ({state}).'

    def bring_window_to_top(self, target_handle: int):
        if not win32gui.IsWindow(target_handle):
            raise ValueError("Invalid window handle")

        try:
            if win32gui.IsIconic(target_handle):
                win32gui.ShowWindow(target_handle, win32con.SW_RESTORE)

            foreground_handle = win32gui.GetForegroundWindow()

            # Validate both handles before proceeding
            if not win32gui.IsWindow(foreground_handle):
                # No valid foreground window, just try to set target as foreground
                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)
                return

            # We attach our own thread (current_tid) to both the foreground and
            # target window threads to make focus change succeed.
            #
            # Simply attaching foreground_thread to target_thread is not sufficient:
            # SetForegroundWindow is called from our MCP thread, and Windows requires
            # the calling process to satisfy the "received the last input event"
            # criterion. Without attaching current_tid, the system may only bring the
            # target window to the front but refuse to transfer keyboard
            # focus, causing subsequent keyboard input to remain in the previous window.
            #
            # By attaching current_tid to both threads, our thread shares
            # their input state and inherits that eligibility, allowing the system to
            # grant the focus switch.
            foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground_handle)
            target_thread, _ = win32process.GetWindowThreadProcessId(target_handle)
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()

            if not foreground_thread or not target_thread or foreground_thread == target_thread:
                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)
                return

            ctypes.windll.user32.AllowSetForegroundWindow(-1)

            attached_threads = []
            try:
                for thread in (foreground_thread, target_thread):
                    if thread and thread != current_tid:
                        try:
                            win32process.AttachThreadInput(current_tid, thread, True)
                            attached_threads.append(thread)
                        except Exception as e:
                            # AttachThreadInput fails with Access Denied for elevated
                            # processes (e.g. Settings, Task Manager). Skip the attach
                            # and still attempt SetForegroundWindow below.
                            logger.debug(
                                f"AttachThreadInput failed for thread {thread} "
                                f"(likely elevated process), skipping: {e}"
                            )

                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)

                win32gui.SetWindowPos(
                    target_handle,
                    win32con.HWND_TOP,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )

            finally:
                for tid in reversed(attached_threads):
                    win32process.AttachThreadInput(current_tid, tid, False)

        except Exception as e:
            logger.exception(f"Failed to bring window to top: {e}")

    def get_coordinates_from_label(self, label: int) -> tuple[int, int]:
        tree_state = self.desktop_state.tree_state
        if label < len(tree_state.interactive_nodes):
            element_node = tree_state.interactive_nodes[label]
        else:
            scroll_idx = label - len(tree_state.interactive_nodes)
            if scroll_idx < len(tree_state.scrollable_nodes):
                element_node = tree_state.scrollable_nodes[scroll_idx]
            else:
                raise IndexError(f"Label {label} out of range")
        return element_node.center.x, element_node.center.y

    def get_coordinates_from_labels(self, labels: list[int]) -> list[tuple[int, int]]:
        """Resolve multiple UI element labels to screen coordinates in bulk."""
        tree_state = self.desktop_state.tree_state
        interactive_nodes = tree_state.interactive_nodes
        scrollable_nodes = tree_state.scrollable_nodes
        interactive_len = len(interactive_nodes)

        results = []
        for label in labels:
            if label < interactive_len:
                element_node = interactive_nodes[label]
            else:
                scroll_idx = label - interactive_len
                if scroll_idx < len(scrollable_nodes):
                    element_node = scrollable_nodes[scroll_idx]
                else:
                    raise IndexError(f"Label {label} out of range")
            results.append((element_node.center.x, element_node.center.y))
        return results

    def click(self, loc: tuple[int, int] | list[int], button: str = "left", clicks: int = 1):
        """Real mouse click(s) through SendInput; ``clicks=0`` only moves the pointer (hover)."""
        if isinstance(loc, list):
            x, y = loc[0], loc[1]
        else:
            x, y = loc
        flash_overlay.cancel_active_flash()
        native_input.click(int(x), int(y), button=button, count=clicks)

    # Text longer than this is pasted in "auto" mode. Typing uses batched SendInput Unicode
    # events (exact, IME-proof, no clipboard side effects), which stays reliable for long text
    # -- the old per-key keybd_event path lost keystrokes on loaded VMs -- but pasting a very
    # long text is still much faster.
    _LONG_TEXT_PASTE_THRESHOLD = 2000

    def type(
        self,
        loc: tuple[int, int],
        text: str,
        caret_position: Literal["start", "idle", "end"] = "idle",
        clear: bool | str = False,
        press_enter: bool | str = False,
        method: Literal["auto", "keys", "paste"] = "auto",
    ) -> str:
        """Click (x, y), confirm its window really has the keyboard, then type ``text``.

        Returns the input method used. Raises ``FocusLostError`` (nothing typed) if another
        window holds the foreground when the keystrokes would be sent.
        """
        x, y = int(loc[0]), int(loc[1])
        target_window = elements.root_at(x, y)
        flash_overlay.cancel_active_flash()
        native_input.click(x, y)
        sleep(0.05)
        if target_window:
            elements.ensure_foreground(self, target_window)
        if caret_position == "start":
            native_input.press_keys("home")
        elif caret_position == "end":
            native_input.press_keys("end")
        if clear is True or (isinstance(clear, str) and clear.lower() == "true"):
            native_input.press_keys("ctrl+a")
            native_input.press_keys("backspace")
        if target_window:
            elements.ensure_foreground(self, target_window)
        if method == "paste" or (method == "auto" and len(text) > self._LONG_TEXT_PASTE_THRESHOLD):
            self._paste_text(text)
            used = "paste"
        else:
            native_input.type_text(text)
            used = "keys"
        if press_enter is True or (isinstance(press_enter, str) and press_enter.lower() == "true"):
            native_input.press_keys("enter")
        return used

    def _paste_text(self, text: str):
        """Put text on the clipboard, press Ctrl+V, then restore the previous clipboard text.

        Only a previous *text* clipboard can be restored; images/files on the clipboard are lost.
        """
        prior = None
        try:
            prior = uia.GetClipboardText()
        except Exception:
            pass
        uia.SetClipboardText(text)
        # Tiny pause so the OS clipboard write settles before Ctrl+V reads.
        sleep(0.05)
        native_input.press_keys("ctrl+v")
        sleep(0.15)
        # Restore prior clipboard so we don't surprise other tools.
        if prior is not None:
            try:
                uia.SetClipboardText(prior)
            except Exception:
                pass

    def scroll(
        self,
        loc: tuple[int, int] = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> str | None:
        if type == "vertical" and direction not in ("up", "down"):
            return 'Invalid direction. Use "up" or "down".'
        if type == "horizontal" and direction not in ("left", "right"):
            return 'Invalid direction. Use "left" or "right".'
        if type not in ("vertical", "horizontal"):
            return 'Invalid type. Use "horizontal" or "vertical".'
        if loc:
            flash_overlay.cancel_active_flash()
            native_input.move_to(int(loc[0]), int(loc[1]))
        # Real wheel events; horizontal uses the tilt wheel (WM_MOUSEHWHEEL) like a touchpad.
        notches = wheel_times if direction in ("up", "right") else -wheel_times
        native_input.wheel(notches, horizontal=(type == "horizontal"))
        return None

    def _normalize_drag_duration(self, duration: float | int | str | None) -> float | None:
        if duration is None:
            return None
        if isinstance(duration, bool):
            raise ValueError("duration must be a finite number of seconds")
        try:
            effective_duration = float(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError("duration must be a finite number of seconds") from exc
        if not math.isfinite(effective_duration):
            raise ValueError("duration must be a finite number of seconds")
        if effective_duration < 0 or effective_duration > 10:
            raise ValueError("duration must be between 0 and 10 seconds")
        return effective_duration

    @staticmethod
    def _normalize_drag_point(value: object, name: str) -> tuple[int, int]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"{name} must be a list or tuple of exactly 2 integers [x, y]")
        x, y = value
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (x, y)):
            raise ValueError(f"{name} must contain exactly 2 integers")
        return x, y

    def drag(
        self,
        loc: tuple[int, int] | list[int],
        from_loc: tuple[int, int] | list[int] | None = None,
        duration: float | int | str | None = None,
    ) -> dict[str, object]:
        x, y = self._normalize_drag_point(loc, "loc")
        normalized_from_loc = (
            None if from_loc is None else self._normalize_drag_point(from_loc, "from_loc")
        )
        effective_duration = self._normalize_drag_duration(duration)
        sleep(0.5)
        if normalized_from_loc is None:
            cx, cy = uia.GetCursorPos()
        else:
            cx, cy = normalized_from_loc
        uia.DragDrop(cx, cy, x, y, moveSpeed=1, duration=effective_duration)
        return {
            "start": [cx, cy],
            "end": [x, y],
            "duration": effective_duration,
        }

    def move(self, loc: tuple[int, int]):
        """Glide the pointer to (x, y) through real move events (hover menus/tooltips react)."""
        x, y = loc
        flash_overlay.cancel_active_flash()
        native_input.move_to(int(x), int(y), duration=0.12)

    def shortcut(self, shortcut: str, repeat: int = 1):
        """Press a key combination ("ctrl+s", "alt+f4") or a sequence ("ctrl+k ctrl+s")."""
        native_input.press_keys(shortcut, repeat=repeat)

    def multi_select(self, press_ctrl: bool | str = False, locs: list[tuple[int, int]] = []):
        press_ctrl = press_ctrl is True or (
            isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
        )
        flash_overlay.cancel_active_flash()
        # Ctrl is held for the whole sequence and always released, even if a click fails.
        with native_input.hold("ctrl") if press_ctrl else nullcontext():
            for loc in locs:
                x, y = loc
                native_input.click(int(x), int(y))
                sleep(0.2)

    def multi_edit(self, locs: list[tuple[int, int, str]]):
        for loc in locs:
            x, y, text = loc
            self.type((x, y), text=text, clear=True)

    def scrape(self, url: str) -> str:
        current_url = url
        try:
            for _ in range(5):
                validate_url(current_url)
                response = requests.get(current_url, timeout=10, allow_redirects=False)
                if not response.is_redirect:
                    break
                location = response.headers.get("Location")
                if not location:
                    raise ValueError(f"Redirect from {current_url} has no Location header")
                current_url = urljoin(current_url, location)
            else:
                raise ValueError("Too many redirects while fetching URL")
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"HTTP error for {current_url}: {e}") from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(f"Failed to connect to {current_url}: {e}") from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"Request timed out for {current_url}: {e}") from e
        html = response.text
        content = markdownify(html=html)
        return content

    def is_overlay_window(self, element: uia.Control) -> bool:
        no_children = len(element.GetChildren()) == 0
        is_name = "Overlay" in element.Name.strip()
        return no_children or is_name

    def get_controls_handles(self, optimized: bool = False):
        handles = set()

        # For even more faster results (still under development)
        def callback(hwnd, _):
            try:
                # Validate handle before checking properties
                if (
                    win32gui.IsWindow(hwnd)
                    and win32gui.IsWindowVisible(hwnd)
                    and is_window_on_current_desktop(hwnd)
                ):
                    handles.add(hwnd)
            except Exception:
                # Skip invalid handles without logging (common during window enumeration)
                pass

        win32gui.EnumWindows(callback, None)

        if desktop_hwnd := win32gui.FindWindow("Progman", None):
            handles.add(desktop_hwnd)
        if taskbar_hwnd := win32gui.FindWindow("Shell_TrayWnd", None):
            handles.add(taskbar_hwnd)
        if secondary_taskbar_hwnd := win32gui.FindWindow("Shell_SecondaryTrayWnd", None):
            handles.add(secondary_taskbar_hwnd)
        return handles

    # Tuned retry envelope for transient UIA empty results. The OS
    # briefly returns NULL from GetForegroundWindow during focus
    # transitions, app launches, and notification overlays — three
    # attempts at 100 ms each covers the typical race without
    # noticeably slowing the snapshot path when state is steady.
    _UIA_RETRIES = 3
    _UIA_RETRY_SLEEP_MS = 100

    def get_active_window(self, windows: list[Window] | None = None) -> Window | None:
        """Return the foreground app window, retrying briefly on transient
        empty results.

        GetForegroundWindow can return NULL during focus transitions, app
        launches, and notification-overlay flicker — even when there is a
        visible focused window on screen. Without a retry, Snapshot
        reports "No active window found" and the caller is left blind.
        """
        last_error = None
        for attempt in range(self._UIA_RETRIES):
            try:
                if windows is None:
                    windows, _ = self.get_windows()
                active_window = self.get_foreground_window()
                if active_window is None:
                    # NULL foreground — retry, this often clears on the
                    # next pass once whatever was transitioning settles.
                    sleep(self._UIA_RETRY_SLEEP_MS / 1000.0)
                    continue
                if active_window.ClassName == "Progman":
                    return None
                active_window_handle = active_window.NativeWindowHandle
                for window in windows:
                    if window.handle != active_window_handle:
                        continue
                    return window
                # In case active window is not present in the windows list
                return Window(
                    **{
                        "name": active_window.Name,
                        "is_browser": self.is_window_browser(active_window),
                        "depth": 0,
                        "bounding_box": BoundingBox(
                            left=active_window.BoundingRectangle.left,
                            top=active_window.BoundingRectangle.top,
                            right=active_window.BoundingRectangle.right,
                            bottom=active_window.BoundingRectangle.bottom,
                            width=active_window.BoundingRectangle.width(),
                            height=active_window.BoundingRectangle.height(),
                        ),
                        "status": self.get_window_status(active_window),
                        "handle": active_window_handle,
                        "process_id": active_window.ProcessId,
                    }
                )
            except Exception as ex:
                last_error = ex
                # Same retry policy for transient exceptions —
                # ControlFromHandle(NULL) raises during focus transitions
                # and the next attempt usually succeeds.
                sleep(self._UIA_RETRY_SLEEP_MS / 1000.0)
                continue
        if last_error is not None:
            logger.error(
                f"Error in get_active_window after {self._UIA_RETRIES} retries: {last_error}"
            )
        return None

    def get_foreground_window(self) -> uia.Control | None:
        handle = uia.GetForegroundWindow()
        # NULL handle means no window has foreground focus right now —
        # don't pass that into ControlFromHandle, which would raise.
        if not handle:
            return None
        return self.get_window_from_element_handle(handle)

    def get_window_from_element_handle(self, element_handle: int) -> uia.Control:
        current = uia.ControlFromHandle(element_handle)
        root_handle = uia.GetRootControl().NativeWindowHandle

        while True:
            parent = current.GetParentControl()
            if parent is None or parent.NativeWindowHandle == root_handle:
                return current
            current = parent

    def get_windows(
        self, controls_handles: set[int] | None = None
    ) -> tuple[list[Window], set[int]]:
        try:
            windows = []
            window_handles = set()
            controls_handles = controls_handles or self.get_controls_handles()
            for depth, hwnd in enumerate(controls_handles):
                try:
                    child = uia.ControlFromHandle(hwnd)
                except Exception:
                    continue

                # Filter out Overlays (e.g. NVIDIA, Steam)
                if self.is_overlay_window(child):
                    continue

                if isinstance(child, (uia.WindowControl, uia.PaneControl)):
                    window_pattern = child.GetPattern(uia.PatternId.WindowPattern)
                    if window_pattern is None:
                        continue

                    if window_pattern.CanMinimize and window_pattern.CanMaximize:
                        status = self.get_window_status(child)

                        bounding_rect = child.BoundingRectangle
                        if bounding_rect.isempty() and status != Status.MINIMIZED:
                            continue

                        windows.append(
                            Window(
                                **{
                                    "name": child.Name,
                                    "depth": depth,
                                    "status": status,
                                    "bounding_box": BoundingBox(
                                        left=bounding_rect.left,
                                        top=bounding_rect.top,
                                        right=bounding_rect.right,
                                        bottom=bounding_rect.bottom,
                                        width=bounding_rect.width(),
                                        height=bounding_rect.height(),
                                    ),
                                    "handle": child.NativeWindowHandle,
                                    "process_id": child.ProcessId,
                                    "is_browser": self.is_window_browser(child),
                                }
                            )
                        )
                        window_handles.add(child.NativeWindowHandle)
        except Exception as ex:
            logger.error(f"Error in get_windows: {ex}")
            windows = []
        return windows, window_handles

    def get_screen_size(self) -> Size:
        width, height = uia.GetVirtualScreenSize()
        return Size(width=width, height=height)

    def get_screen_box(self) -> BoundingBox:
        left, top, width, height = uia.GetVirtualScreenRect()
        return BoundingBox(
            left=left,
            top=top,
            right=left + width,
            bottom=top + height,
            width=width,
            height=height,
        )

    @staticmethod
    def parse_display_selection(
        display: int | list[int] | tuple[int, ...] | None,
    ) -> list[int] | None:
        if display is None or display == "":
            return None

        if isinstance(display, bool):
            raise ValueError(
                "display must be a JSON array of zero-based active display indices, for example [0] or [0,1]"
            )

        if isinstance(display, int):
            values = [display]
        elif isinstance(display, (list, tuple)):
            values = list(display)
        else:
            raise ValueError(
                "display must be a JSON array of zero-based active display indices, for example [0] or [0,1]"
            )

        unique_values: list[int] = []
        for value in values:
            if not isinstance(value, int) or value < 0:
                raise ValueError("display must contain only zero-based active display indices")
            if value not in unique_values:
                unique_values.append(value)
        return unique_values or None

    @staticmethod
    def get_displays() -> list[uia.DisplayInfo]:
        return uia.GetDisplays()

    @staticmethod
    def _display_to_view(display: uia.DisplayInfo) -> Display:
        return Display(
            index=display.index,
            device_name=display.device_name,
            bounding_box=Desktop._rect_to_bounding_box(display.rect),
            primary=display.primary,
        )

    def get_display_union_rect(
        self,
        display_indices: list[int],
        displays: list[uia.DisplayInfo] | None = None,
    ) -> uia.Rect:
        displays = displays if displays is not None else self.get_displays()
        if not displays:
            logger.warning(
                "Monitor enumeration returned no monitors while display filter was requested"
            )
            raise ValueError("No displays detected")

        display_by_index = {display.index: display for display in displays}
        invalid_indices = [index for index in display_indices if index not in display_by_index]
        if invalid_indices:
            available_indices = ",".join(str(display.index) for display in displays)
            logger.warning(
                "Invalid display selection %s. Available displays: %s",
                invalid_indices,
                available_indices,
            )
            raise ValueError(
                f"Invalid display index {invalid_indices[0]}. Available displays: {available_indices}"
            )

        selected_rects = [display_by_index[index].rect for index in display_indices]
        return uia.Rect(
            left=min(rect.left for rect in selected_rects),
            top=min(rect.top for rect in selected_rects),
            right=max(rect.right for rect in selected_rects),
            bottom=max(rect.bottom for rect in selected_rects),
        )

    def get_screenshot(self, capture_rect: uia.Rect | None = None) -> Image.Image:
        flash_overlay.cancel_active_flash()
        image, used_backend = screenshot_capture.capture(capture_rect)
        self._last_screenshot_backend = used_backend
        flash_overlay.show_capture_flash(capture_rect)
        return image

    def get_annotated_screenshot(
        self,
        nodes: list[TreeElementNode],
        cursor_pos: tuple[int, int] | None = None,
        grid_lines: tuple[int, int] | None = None,
        capture_rect: uia.Rect | None = None,
    ) -> Image.Image:
        screenshot = self.get_screenshot(capture_rect=capture_rect)
        annotated_screenshot = screenshot.copy()
        draw = ImageDraw.Draw(annotated_screenshot)
        image_width, image_height = annotated_screenshot.size
        font_size = 12
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except IOError:
            font = ImageFont.load_default()

        def get_random_color():
            return "#{:06x}".format(random.randint(0, 0xFFFFFF))

        def clamp(value: float, minimum: float, maximum: float) -> float:
            return max(minimum, min(value, maximum))

        def get_label_size(text: str) -> tuple[int, int]:
            text_box = draw.textbbox((0, 0), text, font=font)
            return text_box[2] - text_box[0] + 4, text_box[3] - text_box[1] + 4

        def draw_label(text: str, x: float, y: float, color: str) -> None:
            label_width, label_height = get_label_size(text)
            label_x = int(clamp(x, 0, max(0, image_width - label_width)))
            label_y = int(clamp(y, 0, max(0, image_height - label_height)))
            draw.rectangle(
                [(label_x, label_y), (label_x + label_width, label_y + label_height)],
                fill=color,
            )
            draw.text((label_x + 2, label_y + 2), text, fill=(255, 255, 255), font=font)

        if capture_rect:
            left_offset, top_offset = capture_rect.left, capture_rect.top
        else:
            left_offset, top_offset, _, _ = uia.GetVirtualScreenRect()

        # Draw grid lines if requested
        if grid_lines:
            w_count, h_count = grid_lines
            for i in range(1, w_count):
                x = image_width * i // w_count
                draw.line([(x, 0), (x, image_height)], fill=(200, 200, 200, 128), width=1)
            for i in range(1, h_count):
                y = image_height * i // h_count
                draw.line([(0, y), (image_width, y)], fill=(200, 200, 200, 128), width=1)

        def draw_annotation(label, node: TreeElementNode):
            box = node.bounding_box
            color = get_random_color()

            adjusted_left = int(box.left - left_offset)
            adjusted_top = int(box.top - top_offset)
            adjusted_right = int(box.right - left_offset)
            adjusted_bottom = int(box.bottom - top_offset)
            clipped_box = (
                int(clamp(adjusted_left, 0, image_width - 1)),
                int(clamp(adjusted_top, 0, image_height - 1)),
                int(clamp(adjusted_right, 0, image_width - 1)),
                int(clamp(adjusted_bottom, 0, image_height - 1)),
            )
            left, top, right, bottom = clipped_box
            if right <= left or bottom <= top:
                return

            draw.rectangle(clipped_box, outline=color, width=2)

            label_text = str(label)
            label_width, label_height = get_label_size(label_text)
            label_x = right - label_width
            label_y = top - label_height - 2
            if label_y < 0:
                label_y = bottom + 2
            draw_label(label_text, label_x, label_y, color)

        # Draw annotations in parallel
        with ThreadPoolExecutor() as executor:
            executor.map(draw_annotation, range(len(nodes)), nodes)

        # Draw cursor highlight if pos provided
        if cursor_pos:
            cx, cy = cursor_pos
            acx = int(cx - left_offset)
            acy = int(cy - top_offset)

            # Draw a distinctive marker (e.g., a circle or crosshair with a box)
            r = 15
            draw.ellipse([acx - r, acy - r, acx + r, acy + r], outline="red", width=3)
            draw.line([acx - r, acy, acx + r, acy], fill="red", width=2)
            draw.line([acx, acy - r, acx, acy + r], fill="red", width=2)

            # Draw "Cursor" label
            c_label = "CURSOR"
            draw_label(c_label, acx + r, acy - r, "red")

        return annotated_screenshot

    @staticmethod
    def _rect_to_bounding_box(rect: uia.Rect | None) -> BoundingBox | None:
        if rect is None:
            return None
        return BoundingBox(
            left=rect.left,
            top=rect.top,
            right=rect.right,
            bottom=rect.bottom,
            width=rect.width(),
            height=rect.height(),
        )

    @staticmethod
    def _point_in_region(point: tuple[int, int], region: BoundingBox) -> bool:
        x, y = point
        return region.left <= x < region.right and region.top <= y < region.bottom

    @staticmethod
    def _clip_bounding_box_to_region(
        box: BoundingBox | None, region: BoundingBox
    ) -> BoundingBox | None:
        if box is None:
            return None
        left = max(box.left, region.left)
        top = max(box.top, region.top)
        right = min(box.right, region.right)
        bottom = min(box.bottom, region.bottom)
        if right <= left or bottom <= top:
            return None
        return BoundingBox(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            width=right - left,
            height=bottom - top,
        )

    def _filter_window_to_region(self, window: Window | None, region: BoundingBox) -> Window | None:
        if window is None:
            return None
        clipped_box = self._clip_bounding_box_to_region(window.bounding_box, region)
        if clipped_box is None:
            return None
        return Window(
            name=window.name,
            is_browser=window.is_browser,
            depth=window.depth,
            status=window.status,
            bounding_box=clipped_box,
            handle=window.handle,
            process_id=window.process_id,
        )

    def _filter_windows_to_region(self, windows: list[Window], region: BoundingBox) -> list[Window]:
        filtered_windows: list[Window] = []
        for window in windows:
            filtered_window = self._filter_window_to_region(window, region)
            if filtered_window is not None:
                filtered_windows.append(filtered_window)
        return filtered_windows

    def _filter_tree_node_to_region(
        self, node: TreeElementNode, region: BoundingBox
    ) -> TreeElementNode | None:
        clipped_box = self._clip_bounding_box_to_region(node.bounding_box, region)
        if clipped_box is None:
            return None
        return TreeElementNode(
            name=node.name,
            control_type=node.control_type,
            window_name=node.window_name,
            bounding_box=clipped_box,
            center=clipped_box.get_center(),
            metadata=node.metadata,
        )

    def _filter_scroll_node_to_region(self, node, region: BoundingBox):
        clipped_box = self._clip_bounding_box_to_region(node.bounding_box, region)
        if clipped_box is None:
            return None
        return node.__class__(
            name=node.name,
            control_type=node.control_type,
            window_name=node.window_name,
            bounding_box=clipped_box,
            center=clipped_box.get_center(),
            metadata=node.metadata,
        )

    def _filter_semantic_node_to_region(
        self,
        node: SemanticNode | None,
        region: BoundingBox,
    ) -> SemanticNode | None:
        if node is None:
            return None

        clipped_box = None
        if node.bounding_box is not None:
            clipped_box = self._clip_bounding_box_to_region(node.bounding_box, region)
            if clipped_box is None:
                return None

        filtered_children = []
        for child in node.children:
            filtered_child = self._filter_semantic_node_to_region(child, region)
            if filtered_child is not None:
                filtered_children.append(filtered_child)

        if node.element_type != "desktop" and clipped_box is None and not filtered_children:
            return None

        filtered_node = SemanticNode(
            control_type=node.control_type,
            element_type=node.element_type,
            name=node.name,
            window_name=node.window_name,
            center=clipped_box.get_center() if clipped_box is not None else node.center,
            bounding_box=clipped_box,
            metadata=dict(node.metadata),
        )
        filtered_node.children = filtered_children
        return filtered_node

    def _filter_tree_state_to_region(self, tree_state, region: BoundingBox):
        filtered_interactive_nodes = []
        for node in tree_state.interactive_nodes:
            filtered_node = self._filter_tree_node_to_region(node, region)
            if filtered_node is not None:
                filtered_interactive_nodes.append(filtered_node)

        filtered_scrollable_nodes = []
        for node in tree_state.scrollable_nodes:
            filtered_node = self._filter_scroll_node_to_region(node, region)
            if filtered_node is not None:
                filtered_scrollable_nodes.append(filtered_node)

        filtered_dom_node = None
        if tree_state.dom_node is not None:
            filtered_dom_node = self._filter_scroll_node_to_region(tree_state.dom_node, region)

        filtered_semantic_root = self._filter_semantic_node_to_region(
            tree_state.semantic_tree_root,
            region,
        )
        if filtered_semantic_root is not None:
            filtered_semantic_root.bounding_box = region
            filtered_semantic_root.center = region.get_center()

        return tree_state.__class__(
            status=tree_state.status,
            root_node=TreeElementNode(
                name="Desktop",
                control_type="PaneControl",
                bounding_box=region,
                center=region.get_center(),
                window_name="Desktop",
                metadata={},
            ),
            dom_node=filtered_dom_node,
            interactive_nodes=filtered_interactive_nodes,
            scrollable_nodes=filtered_scrollable_nodes,
            dom_informative_nodes=tree_state.dom_informative_nodes if filtered_dom_node else [],
            capture_sec=tree_state.capture_sec,
            semantic_tree_root=filtered_semantic_root,
            truncated=tree_state.truncated,
            element_limit=tree_state.element_limit,
        )
