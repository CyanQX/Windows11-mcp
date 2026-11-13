"""Policy-routed actions. Never retry an already dispatched, unverified action."""

from __future__ import annotations

import json

from windows_mcp.desktop import elements
from windows_mcp.engine.contracts import ActionResult, DispatchEvidence, Status, Verification, TextRangeSpec, normalize_expected
from windows_mcp.engine.target_resolver import resolve
from windows_mcp.engine.verifier import verify
from windows_mcp.engine.pending import ActionInProgress
from windows_mcp.engine.dispatch import trace_dispatch

_CAPABILITY = {"click": "invoke", "invoke": "invoke", "toggle": "toggle", "select": "select",
               "expand": "expand", "collapse": "expand", "set_value": "value", "set_range": "range",
               "scroll_into_view": "scroll_into_view"}


def execute(desktop, runtime, session, *, target: str, action: str, value: str | None = None,
            mode: str = "auto", expected: dict | None = None, text=None) -> dict:
    with trace_dispatch() as trace:
        result = _execute(desktop, runtime, session, target=target, action=action,
                          value=value, mode=mode, expected=expected, text=text)
    evidence = trace.snapshot()
    result.dispatch = DispatchEvidence(**evidence)
    result.input_dispatched = bool(evidence["native_events"] or evidence["uia_calls"])
    mutated = bool(evidence["button_downs"] or evidence["keyboard_events"] or evidence["uia_calls"])
    if result.input_dispatched:
        result.retryable = False
        if result.method == "none":
            result.method = "uia_provider_call" if evidence["uia_calls"] else "win32_sendinput"
    if mutated:
        result.precondition_passed = True
        if result.status in {Status.FAILED, Status.TARGET_STALE, Status.FOCUS_MISMATCH, Status.BLOCKED_BY_POLICY}:
            result.status = Status.DISPATCHED_UNVERIFIED
    return result.model_dump()


def _execute(desktop, runtime, session, *, target: str, action: str, value: str | None = None,
             mode: str = "auto", expected: dict | None = None, text=None) -> ActionResult:
    expected = normalize_expected(expected)
    if text is not None:
        text = TextRangeSpec.model_validate(text).model_dump(mode="json")
        if action not in {"select_text", "replace_text", "delete_text"}:
            raise ValueError("text range options apply only to select_text/replace_text/delete_text")
    if mode not in {"auto", "semantic", "physical", "visual"}:
        raise ValueError("mode must be auto, semantic, physical or visual")
    result = ActionResult(status=Status.FAILED, target=target)
    try:
        if mode == "visual":
            from windows_mcp.vision.adapter import click

            if action != "click" or not value:
                raise ValueError("Visual execution supports click with value='[image_x,image_y]'")
            coordinates = json.loads(value)
            if not isinstance(coordinates, list) or len(coordinates) != 2:
                raise ValueError("Visual coordinates must contain exactly two numbers")
            if expected:
                verify(desktop, runtime, session, expected)
            result.fallback_reason = click(session, target, coordinates)
            result.precondition_passed = True
            result.method = "win32_sendinput_visual"
            result.verification = verify(desktop, runtime, session, expected)
            result.status = Status.VERIFIED if result.verification.passed is True else Status.DISPATCHED_UNVERIFIED
            return result
        element = resolve(desktop, session, target)
        capability = _CAPABILITY.get(action)
        if mode == "semantic":
            via = "uia"
        elif mode == "physical":
            via = "input"
        elif runtime.interaction_mode == "pointer" and action in {"click", "double_click", "right_click", "hover"}:
            via = "input"
        elif action in {"focus", "select_text"} or capability in element.capabilities:
            via = "uia"
        else:
            via = "input"
            result.fallback_reason = "Target does not expose a supported semantic pattern for this action"
        if expected:
            # Validate kinds and file scopes before dispatch, including a missing output file.
            verify(desktop, runtime, session, expected)
        session.check()
        options = {"text": text} if text is not None else {}
        outcome = elements.act(desktop, element, action, value, via=via, **options)
        result.precondition_passed = True
        result.method = outcome.method
        if expected:
            evidence = verify(desktop, runtime, session, expected)
        else:
            evidence = Verification(passed=outcome.verified, evidence_type="control_state" if outcome.verified is not None else "none",
                                    detail="Requested control state was read back" if outcome.verified is True else "Dispatch alone does not establish the task result")
        result.verification = evidence
        result.status = Status.VERIFIED if evidence.passed is True else Status.DISPATCHED_UNVERIFIED
        result.new_revision = session.revision
    except ActionInProgress as exc:
        result.status = Status.DISPATCHED_UNVERIFIED
        result.pending_action_id = exc.action_id
        result.errors = [str(exc)]
        result.retryable = False
    except elements.StaleTarget as exc:
        result.status, result.retryable = Status.TARGET_STALE, True
        result.errors = [str(exc)]
        result.input_dispatched = False
    except elements.AmbiguousTarget as exc:
        result.status = Status.AMBIGUOUS_TARGET
        result.errors = [str(exc)]
        result.input_dispatched = False
    except elements.FocusLostError as exc:
        result.status = Status.FOCUS_MISMATCH
        result.errors = [str(exc)]
        result.input_dispatched = False
    except PermissionError as exc:
        result.status = Status.BLOCKED_BY_POLICY
        result.errors = [str(exc)]
    except (elements.ElementError, ValueError, RuntimeError) as exc:
        result.errors = [str(exc)]
        if session.cancelled.is_set():
            result.status = Status.CANCELLED
    return result
