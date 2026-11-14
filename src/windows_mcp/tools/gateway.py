"""Registration adapter: every MCP call traverses the same policy/audit boundary."""

from __future__ import annotations

import inspect
from functools import wraps

from fastmcp import Context

from windows_mcp.engine.sessions import get_runtime
from windows_mcp.infrastructure.approvals import action_hash
from windows_mcp.infrastructure.authorization import DESKTOP_TOOLS, DispatchAuthorization, bind_dispatch, scope_for_call
from windows_mcp.runtime import run_on_desktop
from windows_mcp.desktop.pointer_motion import bind_motion


# Tools whose calls carry a dispatch authorization down to the code that mutates the machine.
_DISPATCH_BOUND = frozenset(DESKTOP_TOOLS) | {
    "NativeAPI", "FileSystem", "Browser", "PowerShell", "Terminal", "Registry", "Process",
    "Clipboard", "Notification",
}
_FILE_EXPECTATIONS = frozenset({"file_exists", "file_content", "file_sha256"})


def _normalize_expected(arguments: dict, name: str, policy) -> None:
    from windows_mcp.engine.contracts import normalize_expected

    if arguments.get("expected") is not None:
        arguments["expected"] = normalize_expected(arguments["expected"], browser=name == "Browser")


def _normalize_text_range(arguments: dict, name: str, policy) -> None:
    from windows_mcp.engine.contracts import TextRangeSpec

    if arguments.get("text") is not None:
        arguments["text"] = TextRangeSpec.model_validate(arguments["text"]).model_dump(mode="json")


def _check_paths(*keys: str):
    def rule(arguments: dict, name: str, policy) -> None:
        for key in keys:
            if arguments.get(key):
                arguments[key] = str(policy.check_path(arguments[key]))

    return rule


def _scope_expected_files(arguments: dict, name: str, policy) -> None:
    expected = arguments.get("expected")
    if expected and expected.get("kind") in _FILE_EXPECTATIONS:
        arguments["expected"] = {**expected, "path": str(policy.check_path(expected.get("path") or ""))}


# Argument rules run in order before authorization, so the policy sees canonical values.
_ARGUMENT_RULES = {
    "Execute": (_normalize_expected, _normalize_text_range, _scope_expected_files),
    "Verify": (_normalize_expected, _scope_expected_files),
    "Watch": (_normalize_expected, _scope_expected_files),
    "Browser": (_normalize_expected,),
    "Observe": (_normalize_text_range,),
    "FileSystem": (_check_paths("path", "destination"),),
    "NativeAPI": (_check_paths("path", "destination"),),
    "Terminal": (_check_paths("cwd"),),
}


class ToolGateway:
    def __init__(self, mcp, *, runtime=None, get_desktop=lambda: None) -> None:
        self.mcp = mcp
        self.runtime = runtime or get_runtime()
        self.runtime.scope_resolver = lambda tool, arguments, state: scope_for_call(
            tool, arguments, desktop=get_desktop(), session=state, browser=self.runtime.browser
        )

    def tool(self, *, name: str, **options):
        def register(func):
            signature = inspect.signature(func)

            rules = _ARGUMENT_RULES.get(name, ())

            def prepare(bound):
                for rule in rules:
                    rule(bound.arguments, name, self.runtime.policy)
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
                        if name != "Session":
                            from windows_mcp.infrastructure.execution_security import require_standard, require_runtime_available
                            require_standard()
                            require_runtime_available()
                        if name in DESKTOP_TOOLS:
                            self.runtime.pending.check()
                            scope = await run_on_desktop(self.runtime.scope_resolver, name, arguments, state)
                        else:
                            scope = self.runtime.scope_resolver(name, arguments, state)
                        if name != "Session":
                            state.check(generation)
                        risk = self.runtime.policy.authorize(state.session_id, name, arguments, bound.arguments.get("approval_token"), scope=scope)
                        capability_one_use = getattr(risk, "capability_one_use", False)
                        trusted = getattr(risk, "trusted", False)
                        authorization = DispatchAuthorization(self.runtime.policy, state.session_id, name, arguments, scope, risk, trusted, generation, capability_one_use)
                        with self.runtime.policy.network_call(state.session_id, risk, scope), bind_dispatch(authorization if name in _DISPATCH_BOUND else None), bind_motion(self.runtime.motion):
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
