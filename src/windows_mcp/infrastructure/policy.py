"""Fail-closed policy shared by every registered MCP tool, including legacy tools."""

from __future__ import annotations

from pathlib import Path
import json
import os
import contextvars
import threading
import time
from contextlib import contextmanager
from typing import Any

from windows_mcp.infrastructure.approvals import ApprovalStore
from windows_mcp.infrastructure.config import CONFIG_DIR
from windows_mcp.infrastructure.runtime_paths import portable_data_directory
from windows_mcp.infrastructure.trust import TrustStore
from windows_mcp.infrastructure.network import NetworkGrants, origin
from windows_mcp.infrastructure.authorization import DESKTOP_TOOLS, action_name, scope_applications


class PolicyBlocked(PermissionError):
    code = "BLOCKED_BY_POLICY"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class AuthorizationDecision(int):
    """Risk plus the exact evidence used by this authorization, without a reread race."""
    def __new__(cls, risk, trusted, capability_one_use=False, network_origins=()):
        value = super().__new__(cls, risk)
        value.trusted = trusted
        value.capability_one_use = capability_one_use
        value.network_origins = tuple(network_origins)
        return value


_network_call = contextvars.ContextVar("actilo_network_call", default=None)


class PolicyGate:
    def __init__(self, *, roots: list[Path] | None = None, allow_r3: bool = False,
                 allow_private_browser: bool = False, approvals: ApprovalStore | None = None,
                 trust: TrustStore | None = None, allowed_network_origins: list[str] | None = None) -> None:
        self.roots = [path.resolve() for path in (roots or [CONFIG_DIR / "workspace"])]
        self._allow_r3 = allow_r3
        self._allow_private_browser = allow_private_browser
        self.approvals = approvals or ApprovalStore(CONFIG_DIR / "approvals")
        self.trust = trust if trust is not None else TrustStore(self.approvals.directory)
        self.network = NetworkGrants(self.approvals.directory)
        self.allowed_network_origins = {origin(value) for value in (allowed_network_origins or [])}
        self._network_calls = {}
        self._network_lock = threading.Lock()
        self.protected = [CONFIG_DIR.resolve(), Path("~/.codex").expanduser().resolve(),
                          Path("~/.ssh").expanduser().resolve(), self.approvals.directory]
        portable = portable_data_directory()
        self.workspace_storage = {CONFIG_DIR.resolve()}
        if portable is not None:
            self.protected.extend([portable.resolve(), (portable.parent / "WindowsMCP.portable.json").resolve(),
                                   (portable.parent / "Windows-MCP.exe").resolve(), (Path.home() / ".windows-mcp").resolve()])
            self.workspace_storage.add(portable.resolve())
        # Backups can contain client credentials. They and update plans/runtime
        # are operator-owned data even if the workspace is configured broadly.
        self.protected.append((Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "WindowsMCP").resolve())
        try:
            manager = json.loads((CONFIG_DIR / "manager.json").read_text(encoding="utf-8"))
            if isinstance(manager, dict) and isinstance(manager.get("data_dir"), str) and Path(manager["data_dir"]).is_absolute():
                self.protected.append(Path(manager["data_dir"]).resolve())
        except (ValueError, OSError, TypeError):
            pass

    @property
    def allow_r3(self):
        return self._allow_r3 or self.trust.full_access()

    @allow_r3.setter
    def allow_r3(self, value):
        self._allow_r3 = value

    @property
    def allow_private_browser(self):
        return self._allow_private_browser or self.trust.full_access()

    @allow_private_browser.setter
    def allow_private_browser(self, value):
        self._allow_private_browser = value

    def check_path(self, value: str | Path) -> Path:
        from platformdirs import user_desktop_dir

        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(user_desktop_dir()) / path
        # resolve follows symlinks/junctions and normalizes traversal before containment checks.
        path = path.resolve()
        full = self.trust.full_access()
        if not full and not any(path.is_relative_to(root) for root in self.roots):
            raise PolicyBlocked("Path lies outside the operator-configured workspace")
        # The dedicated workspace is the sole allowed subtree inside CONFIG_DIR.
        for protected in ([self.approvals.directory] if full else self.protected):
            if path.is_relative_to(protected) and not (
                protected in self.workspace_storage
                and path.is_relative_to((CONFIG_DIR / "workspace").resolve())
            ):
                raise PolicyBlocked("Credentials, server configuration and approval storage are protected")
        if ":" in path.name or (not full and (path.drive.startswith("\\\\") or any(
            part.lower() in {".git", ".ssh", ".codex", "browser-profiles"} for part in path.parts
        ))):
            raise PolicyBlocked("Network paths, alternate streams and protected metadata are not allowed")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if any(part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved for part in path.parts):
            raise PolicyBlocked("Reserved device names and ambiguous Win32 path components are not allowed")
        return path

    def network_origins(self, tool: str, args: dict, scope=None) -> list[str]:
        use_dom = args.get("use_dom") is True or (isinstance(args.get("use_dom"), str) and args["use_dom"].lower() == "true")
        if (tool == "Scrape" and not use_dom) or (
            tool == "Browser" and args.get("operation") in {"navigate", "new_page"} and args.get("url")
        ):
            return [origin(args["url"])]
        if tool == "Browser" and args.get("operation") in {"click", "fill", "check", "select"}:
            return (scope or {}).get("network_origins", [])
        return []

    def network_base_allowed(self, session_id, url):
        target = origin(url)
        return bool(self.trust.allows_network() or target in self.allowed_network_origins or self.network.allows(session_id, url))

    @contextmanager
    def network_call(self, session_id, decision, scope):
        origins = getattr(decision, "network_origins", ())
        if not origins:
            yield
            return
        key = object()
        with self._network_lock:
            self._network_calls[key] = {"session": session_id, "origins": frozenset(origins),
                "page": (scope or {}).get("page_id"), "expires": time.monotonic() + 120}
        token = _network_call.set((self, key))
        try:
            yield
        finally:
            _network_call.reset(token)
            with self._network_lock:
                self._network_calls.pop(key, None)

    def bind_network_page(self, session_id, page_id):
        current = _network_call.get()
        if current is not None and current[0] is self:
            with self._network_lock:
                row = self._network_calls.get(current[1])
                if row and row["session"] == session_id:
                    row["page"] = page_id

    def check_network(self, session_id: str, url: str, *, page_id=None) -> None:
        target = origin(url)  # Check permission before DNS to avoid leaking data via hostnames.
        if self.network_base_allowed(session_id, url):
            return
        current = _network_call.get()
        with self._network_lock:
            rows = list(self._network_calls.values()) if page_id is not None else (
                [self._network_calls.get(current[1])] if current is not None and current[0] is self else [])
            if any(row and row["session"] == session_id and target in row["origins"]
                   and row["expires"] > time.monotonic() and (page_id is None or row["page"] == page_id) for row in rows):
                return
        raise PolicyBlocked(f"Network origin {target} is not authorized for this call; request exact one-use local approval")

    def trusted(self, session_id: str, tool: str, args: dict, scope: dict) -> bool:
        if self.trust is not None and self.trust.automatic():
            return self.trust.allows(session_id, scope.get("application"), tool, action_name(tool, args))
        applications = scope_applications(scope)
        return (bool(applications) and self.trust is not None
                and all(self.trust.allows(session_id, app, tool, action_name(tool, args)) for app in applications))

    def risk(self, tool: str, args: dict[str, Any], scope: dict | None = None) -> int:
        if tool in DESKTOP_TOOLS:
            if scope and scope.get("sensitive"):
                return 3
            if tool == "Shortcut":
                from windows_mcp.infrastructure.authorization import sensitive_shortcut

                if sensitive_shortcut(args.get("shortcut", "")):
                    return 3
        if tool in {"Observe", "Watch", "Verify", "Session", "RequestApproval", "Find", "Snapshot",
                    "Screenshot", "Visual", "DisplayInventory", "Wait", "WaitFor", "Scrape"}:
            return 0
        if tool in {"FileSystem", "NativeAPI"}:
            operation = args.get("mode") if tool == "FileSystem" else args.get("operation")
            path = self.check_path(args.get("path") or "")
            destination = args.get("destination")
            if destination:
                destination_path = self.check_path(destination)
                if destination_path.suffix.lower() in {".exe", ".dll", ".ps1", ".bat", ".cmd", ".py", ".js", ".lnk", ".reg", ".msi"}:
                    return 3
            if operation in {"read", "list", "search", "info"}:
                return 0
            if operation in {"delete", "move"}:
                protected_paths = [self.approvals.directory] if self.trust.full_access() else self.protected
                if any(protected.is_relative_to(path) for protected in protected_paths):
                    raise PolicyBlocked("Cannot remove a directory containing protected configuration")
                return 3
            if path.suffix.lower() in {".exe", ".dll", ".ps1", ".bat", ".cmd", ".py", ".js", ".lnk", ".reg", ".msi"}:
                return 3
            if operation == "write":
                return 2 if path.exists() else 1
            if operation == "copy" and destination:
                if path.is_dir():
                    raise PolicyBlocked("Recursive copying is disabled; copy individual scope-checked files")
                return 2 if self.check_path(destination).exists() else 1
            raise PolicyBlocked("Unsupported native/file operation")
        if tool in {"PowerShell", "Terminal"}:
            if tool == "Terminal" and args.get("operation") in {"read", "cancel", "close"}:
                return 0
            return 3
        if tool == "Registry":
            return 0 if args.get("mode") in {"get", "list"} else 3
        if tool == "Process":
            return 0 if args.get("mode") == "list" else 3
        if tool == "Browser":
            operation = args.get("operation")
            if operation in {"status", "observe", "read", "navigate", "new_page"}:
                return 0
            return 1 if operation in {"start", "close"} else 2
        if tool == "App":
            mode = args.get("mode")
            if mode in {"switch", "minimize", "maximize", "restore", "resize", "move"}:
                return 1
            return 2 if mode == "close" else 3
        if tool in {"Act", "Execute"}:
            return 1 if args.get("action") in {"focus", "hover", "scroll_into_view"} else 2
        if tool == "Move":
            return 2 if args.get("drag") or args.get("drag_to") else 1
        if tool in {"Scroll", "Clipboard", "Notification"}:
            return 0 if tool == "Clipboard" and args.get("mode") == "get" else 1
        if tool in {"Click", "Type", "Shortcut", "MultiSelect", "MultiEdit"}:
            # Raw input can submit, send or delete without a semantic control name.
            # Never infer authorization from a button label supplied by an agent.
            return 2
        raise PolicyBlocked(f"No policy is defined for tool {tool}")

    def authorize(self, session_id: str, tool: str, args: dict, approval_id: str | None = None,
                  *, scope: dict | None = None) -> int:
        risk = self.risk(tool, args, scope)
        destinations = self.network_origins(tool, args, scope)
        network_once = [target for target in destinations if not self.network_base_allowed(session_id, target)]
        if tool in {"Click", "Type", "Shortcut", "MultiSelect", "MultiEdit", "Act", "Execute", "Move", "Scroll", "App", "PowerShell", "Terminal"}:
            from windows_mcp.infrastructure.operator_ui import operator_review_active
            if operator_review_active() and not (tool == "Terminal" and args.get("operation") in {"read", "cancel", "close"}):
                raise PolicyBlocked("The local human approval window is open; MCP input is paused until the operator decides and closes it")
        trusted = risk >= 2 and self.trusted(session_id, tool, args, scope or {})
        capability_one_use = risk == 3 and not self.allow_r3
        if capability_one_use:
            trusted = False  # Disabled capabilities require an exact local one-use decision.
        if (network_once or (risk >= 2 and not trusted)) and not self.approvals.consume(session_id, tool, args, approval_id, authorization_scope=scope):
            raise PolicyBlocked(f"{'Network origin' if network_once else 'R' + str(risk)} requires a one-use local operator approval. Call RequestApproval with this exact tool and arguments")
        return AuthorizationDecision(risk, trusted, capability_one_use, network_once)
