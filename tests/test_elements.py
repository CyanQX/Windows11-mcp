"""elements: matching, references, change reports and the keyboard interlock (no real UI needed)."""

from __future__ import annotations

import pytest

from windows_mcp.desktop import elements as el


def make(name="Save", control_type="Button", rect=(10, 10, 110, 40), runtime_id=(1, 2, 3), **extra) -> el.Element:
    values = dict(
        raw=None,
        name=name,
        control_type=control_type,
        control_type_id=el.normalize_control_type(control_type),
        localized_type=control_type.lower(),
        automation_id="",
        class_name="",
        rect=rect,
        enabled=True,
        offscreen=False,
        focused=False,
        password=False,
        pid=42,
        framework="Win32",
        runtime_id=runtime_id,
        capabilities=frozenset({"invoke"}),
        state={},
        window_handle=0x100,
        window_title="Editor",
    )
    values.update(extra)
    return el.Element(**values)


class TestNameScore:
    @pytest.mark.parametrize(
        ("query", "name", "expected"),
        [("save", "Save", 100), ("Save", "&Save", 100), ("sa", "Save as...", 90), ("as", "Save as...", 82)],
    )
    def test_exact_beats_prefix_beats_substring(self, query, name, expected):
        assert el.name_score(query, make(name=name)) == expected

    def test_automation_id_matches(self):
        assert el.name_score("btnOk", make(name="OK", automation_id="btnOk")) == 95

    def test_fuzzy_match_stays_below_substring_matches(self):
        assert 0 < el.name_score("Setings", make(name="Settings")) < 82

    def test_unrelated_names_do_not_match(self):
        assert el.name_score("delete", make(name="Save")) == 0

    def test_no_query_matches_everything_neutrally(self):
        assert el.name_score(None, make()) == 50


class TestControlTypes:
    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [("button", "Button"), ("ButtonControl", "Button"), ("textbox", "Edit"), ("dropdown", "ComboBox"),
         ("check box", "CheckBox"), ("link", "Hyperlink"), ("list item", "ListItem"), ("dialog", "Window")],
    )  # fmt: skip
    def test_aliases_resolve(self, alias, canonical):
        assert el.short_type(el.normalize_control_type(alias)) == canonical

    def test_unknown_type_lists_the_choices(self):
        with pytest.raises(ValueError, match="button"):
            el.normalize_control_type("spaceship")


class TestRegistry:
    def test_same_element_keeps_its_reference(self):
        registry = el.ElementRegistry()
        first = registry.register(make())
        again = registry.register(make())  # same runtime id, found by a later search
        assert first == again == "e1"
        assert registry.register(make(name="Other", runtime_id=(9,))) == "e2"

    def test_relocated_element_keeps_its_reference(self):
        registry = el.ElementRegistry()
        ref = registry.register(make())
        rebuilt = make(runtime_id=(7, 7))  # new runtime id after the window was rebuilt
        rebuilt.ref = ref
        assert registry.register(rebuilt) == ref

    def test_capacity_evicts_the_oldest(self):
        registry = el.ElementRegistry(capacity=2)
        refs = [registry.register(make(runtime_id=(i,))) for i in range(3)]
        assert registry.get(refs[0]) is None and registry.get(refs[2]) is not None
        assert len(registry) == 2

    def test_lookup_is_case_insensitive(self):
        registry = el.ElementRegistry()
        registry.register(make())
        assert registry.get(" E1 ") is not None


class TestStateText:
    def test_state_is_summarised_for_the_model(self):
        element = make(
            control_type="CheckBox",
            state={"toggle": "on", "read_only": True},
            enabled=False,
            offscreen=True,
        )
        assert element.state_text() == "toggle=on read-only DISABLED offscreen"

    def test_value_and_range(self):
        edit = make(control_type="Edit", state={"value": "line1\nline2"})
        assert edit.state_text() == 'value="line1\\nline2"'
        slider = make(control_type="Slider", state={"range": (65.0, 0.0, 100.0)})
        assert slider.state_text() == "range=65 (0..100)"


class TestChanges:
    def window(self, handle, title):
        return el.TopWindow(handle=handle, title=title, pid=1, class_name="x", minimized=False)

    def test_describes_opened_closed_renamed_foreground_and_focus(self):
        before = el.Observation(self.window(1, "Doc"), {1: "Doc", 2: "Old"}, 'Edit "Body"')
        after = el.Observation(self.window(3, "Save As"), {1: "*Doc", 3: "Save As"}, 'Edit "File name:"')
        changes = el.describe_changes(before, after)
        assert 'window opened: "Save As"' in changes
        assert 'window closed: "Old"' in changes
        assert 'window title changed: "Doc" -> "*Doc"' in changes
        assert 'foreground is now "Save As"' in changes
        assert 'keyboard focus moved to Edit "File name:"' in changes

    def test_no_change_is_reported_honestly(self):
        assert "no window, title or focus change detected" in el.effects_line([])


class TestToggleValues:
    @pytest.mark.parametrize(("value", "expected"), [("on", "on"), ("TRUE", "on"), ("0", "off"), (None, None), ("", None)])
    def test_parse(self, value, expected):
        assert el._parse_toggle(value) == expected

    def test_garbage_is_rejected(self):
        with pytest.raises(ValueError):
            el._parse_toggle("maybe")


def test_act_validates_action_and_via_before_touching_anything():
    with pytest.raises(ValueError, match="action must be one of"):
        el.act(None, make(), "explode")
    with pytest.raises(ValueError, match="via must be one of"):
        el.act(None, make(), "click", via="telepathy")


class TestKeyboardInterlock:
    def test_refuses_to_type_when_the_target_cannot_get_the_foreground(self, monkeypatch):
        brought = []
        monkeypatch.setattr(el.win32gui, "IsWindow", lambda hwnd: True)
        monkeypatch.setattr(el.win32gui, "IsIconic", lambda hwnd: False)
        monkeypatch.setattr(el, "root_of", lambda hwnd: hwnd)
        monkeypatch.setattr(el, "foreground_root", lambda: 0x999)  # someone else keeps focus
        monkeypatch.setattr(el, "_owned_by", lambda hwnd, owner: False)
        monkeypatch.setattr(el, "window_info", lambda hwnd: el.TopWindow(hwnd, f"w{hwnd:x}", 1, "c", False))
        monkeypatch.setattr(el.time, "sleep", lambda s: None)

        class Desktop:
            def bring_window_to_top(self, hwnd):
                brought.append(hwnd)

        with pytest.raises(el.FocusLostError, match="keyboard input was NOT sent"):
            el.ensure_foreground(Desktop(), 0x100)
        assert brought == [0x100]  # it did try to fix the focus first

    def test_passes_when_the_target_is_in_front(self, monkeypatch):
        monkeypatch.setattr(el.win32gui, "IsWindow", lambda hwnd: True)
        monkeypatch.setattr(el, "root_of", lambda hwnd: hwnd)
        monkeypatch.setattr(el, "foreground_root", lambda: 0x100)
        el.ensure_foreground(object(), 0x100)  # no bring_window_to_top needed
