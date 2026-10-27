"""Registration adapter: every MCP call traverses the same policy/audit boundary."""

from __future__ import annotations

import inspect
from functools import wraps

from fastmcp import Context

from windows_mcp.engine.sessions import get_runtime
from windows_mcp.infrastructure.approvals import action_hash
from windows_mcp.infrastructure.authorization import DESKTOP_TOOLS, DispatchAuthorization, bind_dispatch, scope_for_call
from windows_mcp.runtime import run_on_desktop


class ToolGateway:
    def __init__(self, mcp, *, runtime=None, get_desktop=lambda: None) -> None:
        self.mcp = mcp
        self.runtime = runtime or get_runtime()
        self.runtime.scope_resolver = lambda tool, arguments, state: scope_for_call(
            tool, arguments, desktop=get_desktop(), session=state
        )

    def tool(self, *, name: str, **options):
        def register(func):
            signature = inspect.signature(func)

            def prepare(bound):
                from windows_mcp.engine.contracts import TextRangeSpec, normalize_expected

                if name in {"Execute", "Verify", "Watch", "Browser"} and bound.arguments.get("expected") is not None:
                    bound.arguments["expected"] = normalize_expected(bound.arguments["expected"], browser=name == "Browser")
                if name in {"Execute", "Observe"} and bound.arguments.get("text") is not None:
                    bound.arguments["text"] = TextRangeSpec.model_validate(bound.arguments["text"]).model_dump(mode="json")
                if name in {"FileSystem", "NativeAPI"}:
                    for key in ("path", "destination"):
                        if bound.arguments.get(key):
                            bound.arguments[key] = str(self.runtime.policy.check_path(bound.arguments[key]))
                if name in {"Execute", "Verify", "Watch"}:
                    expected = bound.arguments.get("expected")
                    if expected and expected.get("kind") in {"file_exists", "file_content", "file_sha256"}:
                        bound.arguments["expected"] = {**expected, "path": str(self.runtime.policy.check_path(expected.get("path") or ""))}
                return bound

            def normalize(arguments: dict) -> dict:
                bound = signature.bind(**arguments)
                bound.apply_defaults()
                prepare(bound)
                return {key: value for key, value in bound.arguments.items() if key not in {"ctx", "approval_token"}}

            self.runtime.normalizers[name] = normalize

            @wraps(func)
            async def guarded(*args, **kwargs):
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                prepare(bound)
                context = bound.arguments.get("ctx")
                owner = "local"
                if isinstance(context, Context):
                    try:
                        owner = self.runtime.owner(context)
                    except (RuntimeError, AttributeError):
                        owner = str(id(context.session))
                arguments = {key: value for key, value in bound.arguments.items() if key not in {"ctx", "approval_token"}}
                digest = action_hash(name, arguments)
                with self.runtime.bind(owner) as state:
                    generation = state.generation
                    if name != "Session":
                        state.check()
                    audit_id = self.runtime.audit.record(session=state.session_id, tool=name, digest=digest, status="REQUESTED")
                    state.active_calls += 1
                    try:
                        if name in DESKTOP_TOOLS:
                            self.runtime.pending.check()
                            scope = await run_on_desktop(self.runtime.scope_resolver, name, arguments, state)
                        else:
                            scope = self.runtime.scope_resolver(name, arguments, state)
                        if name != "Session":
                            state.check(generation)
                        risk = self.runtime.policy.authorize(state.session_id, name, arguments, bound.arguments.get("approval_token"), scope=scope)
                        trusted = risk == 2 and self.runtime.policy.trusted(state.session_id, name, arguments, scope)
                        authorization = DispatchAuthorization(self.runtime.policy, state.session_id, name, arguments, scope, risk, trusted, generation)
                        with bind_dispatch(authorization if name in DESKTOP_TOOLS | {"NativeAPI", "FileSystem", "Browser", "PowerShell", "Registry", "Process", "Clipboard", "Notification"} else None):
                            result = await func(**bound.arguments)
                    except Exception as exc:
                        self.runtime.audit.record(session=state.session_id, tool=name, digest=digest, status=getattr(exc, "code", "FAILED"), audit_id=audit_id)
                        raise
                    finally:
                        state.active_calls -= 1
                    status = result.get("status", "RETURNED") if isinstance(result, dict) else "RETURNED"
                    self.runtime.audit.record(session=state.session_id, tool=name, digest=digest, status=status, audit_id=audit_id)
                    if isinstance(result, dict) and "status" in result:
                        result["audit_id"] = audit_id
                    return result

            return self.mcp.tool(name=name, **options)(guarded)

        return register
