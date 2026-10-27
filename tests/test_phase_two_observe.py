"""Subtree scope, partial refresh, truthful deltas and executable action names."""

import pytest

from tests.test_elements import make
from tests import test_semantic_engine
from windows_mcp.desktop import elements
from windows_mcp.engine.observe_router import observe_desktop

observed = test_semantic_engine.observed


def test_capabilities_are_separate_from_executable_actions(observed):
    node = observed[4]["elements"][0]
    assert "value" in node["capabilities"] and "value" not in node["actions"]
    assert "set_value" in node["actions"]
    readonly = make(capabilities=frozenset({"value", "range", "text"}), state={"read_only": True, "text_selection_supported": True})
    assert not {"set_value", "type", "set_range", "replace_text"} & set(elements.supported_actions(readonly))
    assert "select_text" in elements.supported_actions(readonly)
    readonly.enabled = False
    assert elements.supported_actions(readonly) == []


def test_target_refresh_reads_live_state_without_walking_tree(observed, monkeypatch):
    desktop, session, _, element, first = observed
    monkeypatch.setattr(elements, "walk_bounded", lambda *a, **k: pytest.fail("refresh must not scan"))
    target = first["elements"][0]["element_id"]
    result = observe_desktop(desktop, session, target=target, mode="refresh")
    assert result["context"]["coverage"] == "known_targets_only"
    assert result["context"]["structural_changes_checked"] is False
    element.state["value"] = "new"
    changed = observe_desktop(desktop, session, mode="refresh", since_revision=result["revision"])
    assert changed["context"]["delta"]
    assert changed["elements"][0]["state"]["value"] == "new"
    assert changed["removed"] == []


def test_subtree_passes_real_root_and_keeps_scope_distinct(observed, monkeypatch):
    desktop, session, _, element, first = observed
    passed = []
    monkeypatch.setattr(elements, "walk_bounded", lambda *a, **kw: (passed.append(kw["root_element"]) or [], False, []))
    result = observe_desktop(desktop, session, target=first["elements"][0]["element_id"])
    assert passed == [element] and result["context"]["coverage"] == "subtree"
    assert len(result["elements"]) == 1
    assert len(session.observations) == 2


def test_disabled_control_can_be_observed_and_verified(observed):
    desktop, session, runtime, element, first = observed
    from windows_mcp.engine.verifier import verify
    element.enabled = False
    target = first["elements"][0]["element_id"]
    assert observe_desktop(desktop, session, target=target, mode="refresh")["elements"][0]["enabled"] is False
    assert verify(desktop, runtime, session, {"kind": "element_state", "target": target, "state": {"enabled": False}}).passed


def test_text_capability_probe_is_lazy_and_enables_only_confirmed_actions(observed, monkeypatch):
    from types import SimpleNamespace

    desktop, session, _, element, _ = observed
    element.capabilities = frozenset({"text"})
    calls = []
    pattern = SimpleNamespace(SupportedTextSelection=1,
        DocumentRange=SimpleNamespace(GetAttributeValue=lambda attribute: False))
    monkeypatch.setattr(elements, "get_pattern", lambda *args: calls.append(True) or pattern)
    full = observe_desktop(desktop, session, window="100")
    assert calls == [] and "replace_text" not in full["elements"][0]["actions"]
    refreshed = observe_desktop(desktop, session, target=full["elements"][0]["element_id"], mode="refresh")
    assert calls == [True]
    assert {"select_text", "replace_text", "delete_text", "append_text"} <= set(refreshed["elements"][0]["actions"])


def test_range_postcondition_survives_json_tuple_conversion(observed):
    desktop, session, runtime, element, first = observed
    from windows_mcp.engine.verifier import verify

    element.state["range"] = (5.0, 0.0, 10.0)
    expected = {"kind": "element_state", "target": first["elements"][0]["element_id"], "state": {"range": [5, 0, 10]}}
    assert verify(desktop, runtime, session, expected).passed
