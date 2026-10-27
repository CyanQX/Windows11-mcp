"""Fail-closed policy shared by every registered MCP tool, including legacy tools."""

from __future__ import annotations

from pathlib import Path
import json
import os
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


class PolicyGate:
    def __init__(self, *, roots: list[Path] | None = None, allow_r3: bool = False,
                 allow_private_browser: bool = False, approvals: ApprovalStore | None = None,
                 trust: TrustStore | None = None, allowed_network_origins: list[str] | None = None) -> None:
        self.roots = [path.resolve() for path in (roots or [CONFIG_DIR / "workspace"])]
        self.allow_r3 = allow_r3
        self.allow_private_browser = allow_private_browser
        self.approvals = approvals or ApprovalStore(CONFIG_DIR / "approvals")
        self.trust = trust if trust is not None else TrustStore(self.approvals.directory)
        self.network = NetworkGrants(self.approvals.directory)
        self.allowed_network_origins = {origin(value) for value in (allowed_network_origins or [])}
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

    def check_path(self, value: str | Path) -> Path:
        from platformdirs import user_desktop_dir

        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(user_desktop_dir()) / path
        # resolve follows symlinks/junctions and normalizes traversal before containment checks.
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise PolicyBlocked("Path lies outside the operator-configured workspace")
        # The dedicated workspace is the sole allowed subtree inside CONFIG_DIR.
        for protected in self.protected:
            if path.is_relative_to(protected) and not (
                protected in self.workspace_storage
                and path.is_relative_to((CONFIG_DIR / "workspace").resolve())
            ):
                raise PolicyBlocked("Credentials, server configuration and approval storage are protected")
        if path.drive.startswith("\\\\") or ":" in path.name or any(
            part.lower() in {".git", ".ssh", ".codex", "browser-profiles"} for part in path.parts
        ):
            raise PolicyBlocked("Network paths, alternate streams and protected metadata are not allowed")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if any(part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved for part in path.parts):
            raise PolicyBlocked("Reserved device names and ambiguous Win32 path components are not allowed")
        return path

    def network_origins(self, tool: str, args: dict) -> list[str]:
        use_dom = args.get("use_dom") is True or (isinstance(args.get("use_dom"), str) and args["use_dom"].lower() == "true")
        if (tool == "Scrape" and not use_dom) or (
            tool == "Browser" and args.get("operation") in {"navigate", "new_page"} and args.get("url")
        ):
            return [origin(args["url"])]
        return []

    def check_network(self, session_id: str, url: str) -> None:
        target = origin(url)  # Check permission before DNS to avoid leaking data via hostnames.
        if target not in self.allowed_network_origins and not self.network.allows(session_id, url):
            raise PolicyBlocked(f"Network origin {target} is not authorized for this session; request local operator approval")

    def trusted(self, session_id: str, tool: str, args: dict, scope: dict) -> bool:
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
                if any(protected.is_relative_to(path) for protected in self.protected):
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
        if tool == "PowerShell":
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
        for target in self.network_origins(tool, args):
            self.check_network(session_id, target)
        if tool in {"Click", "Type", "Shortcut", "MultiSelect", "MultiEdit", "Act", "Execute", "Move", "Scroll", "App", "PowerShell"}:
            from windows_mcp.infrastructure.operator_ui import operator_review_active
            if operator_review_active():
                raise PolicyBlocked("The local human approval window is open; MCP input is paused until the operator decides and closes it")
        if risk == 3 and not self.allow_r3:
            raise PolicyBlocked("R3 tools are disabled; an operator must enable policy.allow_r3 locally")
        trusted = risk == 2 and self.trusted(session_id, tool, args, scope or {})
        if risk >= 2 and not trusted and not self.approvals.consume(session_id, tool, args, approval_id, authorization_scope=scope):
            raise PolicyBlocked(f"R{risk} requires a one-use local operator approval. Call RequestApproval with this exact tool and arguments")
        return risk
