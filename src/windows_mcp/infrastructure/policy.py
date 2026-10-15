"""Fail-closed policy shared by every registered MCP tool, including legacy tools."""

from __future__ import annotations

from pathlib import Path
import json
import os
from typing import Any

from windows_mcp.infrastructure.approvals import ApprovalStore
from windows_mcp.infrastructure.config import CONFIG_DIR


class PolicyBlocked(PermissionError):
    code = "BLOCKED_BY_POLICY"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class PolicyGate:
    def __init__(self, *, roots: list[Path] | None = None, allow_r3: bool = False,
                 allow_private_browser: bool = False, approvals: ApprovalStore | None = None) -> None:
        self.roots = [path.resolve() for path in (roots or [CONFIG_DIR / "workspace"])]
        self.allow_r3 = allow_r3
        self.allow_private_browser = allow_private_browser
        self.approvals = approvals or ApprovalStore(CONFIG_DIR / "approvals")
        self.protected = [CONFIG_DIR.resolve(), Path("~/.codex").expanduser().resolve(),
                          Path("~/.ssh").expanduser().resolve(), self.approvals.directory]
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
                protected == CONFIG_DIR.resolve()
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

    def risk(self, tool: str, args: dict[str, Any]) -> int:
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

    def authorize(self, session_id: str, tool: str, args: dict, approval_id: str | None = None) -> int:
        risk = self.risk(tool, args)
        if tool in {"Click", "Type", "Shortcut", "MultiSelect", "MultiEdit", "Act", "Execute", "Move", "Scroll", "App", "PowerShell"}:
            from windows_mcp.infrastructure.operator_ui import operator_review_active
            if operator_review_active():
                raise PolicyBlocked("The local human approval window is open; MCP input is paused until the operator decides and closes it")
        if risk == 3 and not self.allow_r3:
            raise PolicyBlocked("R3 tools are disabled; an operator must enable policy.allow_r3 locally")
        if risk >= 2 and not self.approvals.consume(session_id, tool, args, approval_id):
            raise PolicyBlocked(f"R{risk} requires a one-use local operator approval. Call RequestApproval with this exact tool and arguments")
        return risk
