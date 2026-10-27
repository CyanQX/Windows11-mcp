"""Server-derived application scopes and dispatch-time checks shared by UIA and input."""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DESKTOP_TOOLS = frozenset({"Click", "Type", "Shortcut", "MultiSelect", "MultiEdit", "Act", "Execute", "Move", "Scroll", "App"})
TRUST_TOOLS = DESKTOP_TOOLS | {"Browser", "FileSystem", "NativeAPI"}
SENSITIVE_PROCESSES = frozenset({"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe",
    "regedit.exe", "mmc.exe", "taskmgr.exe", "wscript.exe", "cscript.exe", "mshta.exe",
    "searchhost.exe", "startmenuexperiencehost.exe"})


def window_scope(hwnd: int) -> dict:
    import psutil
    import win32gui
    import win32process
    from windows_mcp.infrastructure.policy import PolicyBlocked

    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            raise ValueError("Window no longer exists")
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        executable = str(Path(process.exe()).resolve()).casefold()
        class_name = win32gui.GetClassName(hwnd)
        sensitive = (Path(executable).name in SENSITIVE_PROCESSES
                     or class_name in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "VirtualConsoleClass"}
                     or (class_name == "#32770" and win32gui.GetWindowText(hwnd) in {"Run", "运行", "執行"}))
        return {"application": executable, "window": hwnd, "pid": pid,
                "process_started": process.create_time(), "sensitive": sensitive}
    except (OSError, ValueError, psutil.Error) as exc:
        raise PolicyBlocked("Cannot establish the target application's identity; observe again") from exc


def scope_for_call(tool: str, arguments: dict, *, desktop, session) -> dict:
    if tool not in DESKTOP_TOOLS:
        return {"application": "native:filesystem" if tool in {"NativeAPI", "FileSystem"} else f"provider:{tool.lower()}"}
    if tool == "App" and arguments.get("mode") in {"launch", "launch_executable"}:
        return {"application": "desktop:launch"}
    if desktop is None:
        return {"application": "unresolved"}
    from windows_mcp.desktop import elements

    handles = []
    if tool == "Execute":
        target = arguments.get("target", "")
        if target.startswith("visual:"):
            record = session.visuals.get(target)
            if record:
                handles.append(record[0].window_handle)
        else:
            record = session.targets.get(target)
            if record:
                handles.append(record["handle"])
        if not handles:
            raise elements.StaleTarget("TARGET_STALE: observe the target before requesting authorization")
    elif tool == "Act":
        from windows_mcp.tools.element import resolve_target

        element = resolve_target(desktop, arguments["target"], window=arguments.get("window"), control_type=arguments.get("control_type"))
        handles.append(element.window_handle or elements.root_at(*element.center))
    elif tool == "App" or arguments.get("window"):
        windows = elements.resolve_windows(arguments.get("handle") or arguments.get("window") or arguments.get("name"))
        if len(windows) != 1:
            raise elements.AmbiguousTarget("Choose exactly one target window before requesting authorization")
        handles.append(windows[0].handle)
    else:
        label = arguments.get("label")
        if label is not None:
            element = desktop.locate_label(label)
            if element:
                handles.append(element.window_handle or elements.root_at(*element.center))
            else:
                handles.append(elements.root_at(*desktop.get_coordinates_from_labels([label])[0]))
        labels = arguments.get("labels")
        if isinstance(labels, str):
            labels = json.loads(labels)
        if labels:
            ids = [item[0] if isinstance(item, list) else item for item in labels]
            handles.extend(elements.root_at(*point) for point in desktop.get_coordinates_from_labels(ids))
        locations = arguments.get("locs") or []
        if isinstance(locations, str):
            locations = json.loads(locations)
        for key in ("loc", "from_loc", "drag_to"):
            point = arguments.get(key)
            if isinstance(point, str):
                point = json.loads(point)
            if point:
                locations = [*locations, point]
        handles.extend(elements.root_at(int(point[0]), int(point[1])) for point in locations)
    if not handles:
        handles.append(elements.foreground_root())
    scopes = [window_scope(handle) for handle in dict.fromkeys(handles)]
    # The exact windows are bound to a one-use approval. Trust is application-scoped.
    result = dict(scopes[0])
    result["applications"] = sorted({item["application"] for item in scopes})
    result["windows"] = scopes
    result["sensitive"] = any(item["sensitive"] for item in scopes)
    return result


def sensitive_shortcut(spec: str) -> bool:
    from windows_mcp.desktop import native_input

    for chord in native_input.parse_sequence(spec):
        keys = set(chord.modifiers) | set(chord.keys)
        win = bool(keys & {0x5B, 0x5C})
        if ((win and (len(keys) == 1 or bool(keys & {ord("R"), ord("X"), ord("S"), ord("Q")})))
                or ({0x11, 0x1B} <= keys)):
            return True
    return False


def scope_applications(scope: dict) -> list[str]:
    return scope.get("applications") or ([scope["application"]] if scope.get("application") else [])


@dataclass
class DispatchAuthorization:
    policy: object
    session_id: str
    tool: str
    arguments: dict
    scope: dict
    risk: int
    trusted: bool
    generation: int | None = None

    def check(self) -> None:
        from windows_mcp.engine.sessions import get_runtime
        from windows_mcp.infrastructure.policy import PolicyBlocked

        runtime = get_runtime()
        session = runtime.current()
        session.check(self.generation)
        if session.session_id != self.session_id:
            raise PolicyBlocked("Authorization belongs to a different client session")
        if self.tool in DESKTOP_TOOLS:
            runtime.pending.check()
        from windows_mcp.infrastructure.operator_ui import operator_review_active

        if self.tool in DESKTOP_TOOLS and operator_review_active():
            raise PolicyBlocked("Local operator review is open; input is paused")
        if self.risk == 3 and not self.policy.allow_r3:
            raise PolicyBlocked("R3 was disabled after authorization")
        if self.trusted and not self.policy.trusted(self.session_id, self.tool, self.arguments, self.scope):
            raise PolicyBlocked("Scoped automatic authorization expired or was revoked")

    def check_window(self, hwnd: int) -> None:
        from windows_mcp.infrastructure.policy import PolicyBlocked

        self.check()
        actual = window_scope(hwnd)
        if actual["sensitive"] and self.risk < 3:
            raise PolicyBlocked("Sensitive target requires a separate R3 approval")
        if self.risk == 3 and self.scope.get("application") == "desktop:launch":
            return
        if actual["application"] not in scope_applications(self.scope):
            raise PolicyBlocked("Target application changed after authorization; request approval again")
        if not self.trusted:
            windows = self.scope.get("windows", [self.scope])
            matched = any(all(item.get(key) == actual.get(key) for key in ("window", "pid", "process_started")) for item in windows)
            if not matched:
                from windows_mcp.desktop import elements

                for item in windows:
                    if (item.get("pid") == actual["pid"] and item.get("process_started") == actual["process_started"]
                            and item.get("window") and elements._owned_by(actual["window"], item["window"])):
                        original = window_scope(item["window"])
                        matched = all(item.get(key) == original.get(key) for key in ("window", "pid", "process_started", "application"))
                        if matched:
                            break
            if not matched:
                raise PolicyBlocked("Target window lifetime changed after one-use approval")


def action_name(tool: str, arguments: dict) -> str:
    return str(arguments.get("action") or arguments.get("operation") or arguments.get("mode") or tool.lower())


_dispatch: contextvars.ContextVar[DispatchAuthorization | None] = contextvars.ContextVar("wmcp_dispatch", default=None)


@contextmanager
def bind_dispatch(authorization: DispatchAuthorization | None):
    token = _dispatch.set(authorization)
    try:
        yield
    finally:
        _dispatch.reset(token)


def check_window_dispatch(hwnd: int) -> None:
    authorization = _dispatch.get()
    if authorization is not None:
        authorization.check_window(hwnd)


def check_dispatch() -> None:
    authorization = _dispatch.get()
    if authorization is not None:
        authorization.check()


def check_shortcut_dispatch(spec: str) -> None:
    authorization = _dispatch.get()
    if authorization is not None and sensitive_shortcut(spec) and authorization.risk < 3:
        from windows_mcp.infrastructure.policy import PolicyBlocked

        raise PolicyBlocked("System launch/search shortcut requires R3 approval")
