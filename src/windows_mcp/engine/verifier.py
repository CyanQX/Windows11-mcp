"""Independent business postconditions. Mere UI/focus changes never pass verification."""

from __future__ import annotations

import hashlib

from windows_mcp.desktop import elements
from windows_mcp.engine.contracts import Verification, normalize_expected
from windows_mcp.engine.target_resolver import resolve


def verify(desktop, runtime, session, expected: dict | None, *, resolved_element=None) -> Verification:
    expected = normalize_expected(expected)
    if expected is None:
        return Verification()
    kind = expected.get("kind")
    if kind in {"file_exists", "file_content", "file_sha256"}:
        path = runtime.policy.check_path(expected.get("path") or "")
        passed = path.is_file()
        if passed and kind != "file_exists":
            if path.stat().st_size > 10_000_000:
                raise ValueError("Verification file exceeds the 10 MB limit")
            data = path.read_bytes()
            if kind == "file_content":
                passed = data.decode(expected.get("encoding", "utf-8")) == expected.get("content")
            else:
                passed = hashlib.sha256(data).hexdigest() == expected.get("sha256")
        return Verification(passed=passed, evidence_type=kind, detail="File postcondition matched" if passed else "File postcondition did not match")
    if kind == "window_exists":
        if not expected.get("window"):
            raise ValueError("window_exists requires an explicit window")
        windows = elements.resolve_windows(expected["window"])
        return Verification(passed=len(windows) == 1, evidence_type=kind, detail="Exactly one matching live window" if len(windows) == 1 else "Window is missing or ambiguous")
    if kind == "element_state":
        target = expected.get("target")
        wanted = expected.get("state")
        if not target or not isinstance(wanted, dict) or not wanted:
            raise ValueError("element_state requires target and a nonempty state object")
        element = resolved_element or resolve(desktop, session, target, require_enabled=False)
        actual = {**element.state, "enabled": element.enabled, "visible": not element.offscreen,
                  "focused": element.focused}
        if element.password and "value" in wanted:
            return Verification(evidence_type=kind, detail="Sensitive field values are not read back")
        passed = all(key in actual and (list(actual[key]) if key == "range" and isinstance(actual[key], (tuple, list)) else actual[key]) == value
                     for key, value in wanted.items())
        return Verification(passed=passed, evidence_type=kind, detail="Requested control state matched" if passed else "Requested control state did not match")
    raise ValueError("Unknown postcondition; use file_exists, file_content, file_sha256 or element_state/window_exists")
