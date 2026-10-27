"""Text range selection/editing against a mutable provider; no native input is sent."""

from types import SimpleNamespace

import pytest

from tests.test_elements import make
from windows_mcp.desktop import elements as el, text_ranges as tr
from windows_mcp.engine.contracts import TextRangeSpec


class Range:
    def __init__(self, provider, start=0, end=None):
        self.provider, self.start = provider, start
        self.end = len(provider.text) if end is None else end

    def Clone(self):
        return Range(self.provider, self.start, self.end)

    def GetText(self, maximum):
        return self.provider.text[self.start:self.end][:maximum]

    def CompareEndpoints(self, endpoint, other, other_endpoint):
        return (self.end if endpoint else self.start) - (other.end if other_endpoint else other.start)

    def MoveEndpointByRange(self, endpoint, other, other_endpoint):
        position = other.end if other_endpoint else other.start
        if endpoint:
            self.end = position
            self.start = min(self.start, position)
        else:
            self.start = position
            self.end = max(self.end, position)

    def FindText(self, text, backward, ignore_case):
        haystack = self.provider.text[self.start:self.end]
        index = haystack.casefold().find(text.casefold()) if ignore_case else haystack.find(text)
        return None if index < 0 else Range(self.provider, self.start + index, self.start + index + len(text))

    def MoveEndpointByUnit(self, endpoint, unit, count):
        assert unit == 0  # Unit provider exercised with characters; Word is tested on Windows.
        before = self.end if endpoint else self.start
        after = max(0, min(len(self.provider.text), before + count))
        self.MoveEndpointByRange(endpoint, Range(self.provider, after, after), 0)
        return after - before

    def Select(self):
        self.provider.selected = [self.Clone()]

    def GetAttributeValue(self, attribute):
        return self.provider.readonly


class Provider:
    def __init__(self, text="one two one"):
        self.text, self.selected, self.readonly = text, [], False
        self.SupportedTextSelection = 1

    @property
    def DocumentRange(self):
        return Range(self)

    def GetSelection(self):
        return SimpleNamespace(Length=len(self.selected), GetElement=lambda i: self.selected[i])


@pytest.fixture
def provider(monkeypatch):
    provider = Provider()
    element = make(control_type="Edit", state={"read_only": False}, capabilities=frozenset({"text", "value"}),
                   raw=SimpleNamespace(SetFocus=lambda: None))
    monkeypatch.setattr(el, "get_pattern", lambda *args: provider)
    monkeypatch.setattr(el, "call_with_timeout", lambda func, **kwargs: (True, func()))
    monkeypatch.setattr(el, "ensure_foreground", lambda *args: None)
    monkeypatch.setattr(el, "refresh", lambda old: old)
    monkeypatch.setattr(tr, "_focus_matches", lambda old: True)
    sent = []

    def replace(text, **kwargs):
        sent.append(text)
        chosen = provider.selected[0]
        provider.text = provider.text[:chosen.start] + text + provider.text[chosen.end:]

    monkeypatch.setattr(tr.native_input, "type_text", replace)
    monkeypatch.setattr(tr.native_input, "press_keys", lambda key: replace(""))
    return element, provider, sent


def test_ambiguous_text_is_not_selected_or_edited(provider):
    element, pattern, sent = provider
    with pytest.raises(el.AmbiguousTarget, match="occurrence"):
        tr.act(None, element, "replace_text", "new", spec={"text": "one"})
    assert not sent and not pattern.selected


def test_one_based_occurrence_replaces_only_selected_match(provider):
    element, pattern, sent = provider
    outcome = tr.act(None, element, "replace_text", "三 🌍", spec={"text": "one", "occurrence": 2})
    assert outcome.verified is True and pattern.text == "one two 三 🌍"
    assert sent == ["三 🌍"]


def test_literal_selection_and_readback(provider):
    element, pattern, sent = provider
    assert tr.act(None, element, "select_text", "two").verified is True
    assert tr.read(element, selection=True)["selection"] == ["two"]
    assert tr.read(element, max_chars=4) == {"text": "one ", "text_truncated": True, "text_source": "uia_text_pattern"}
    assert not sent


def test_character_range_can_be_read_without_selecting(provider):
    element, pattern, sent = provider
    assert tr.read(element, spec={"unit": "character", "index": 5, "count": 3})["text"] == "two"
    assert not pattern.selected and not sent


def test_delete_and_append_are_verified(provider):
    element, pattern, sent = provider
    assert tr.act(None, element, "delete_text", None, spec={"text": "two "}).verified
    assert pattern.text == "one one"
    assert tr.act(None, element, "append_text", "!").verified
    assert pattern.text == "one one!"


@pytest.mark.parametrize("case", ["password", "readonly", "unknown_readonly", "focus", "changed_text", "changed_selection"])
def test_no_replacement_for_unconfirmed_target(provider, monkeypatch, case):
    element, pattern, sent = provider
    if case == "password":
        element.password = True
    elif case == "readonly":
        pattern.readonly = True
    elif case == "unknown_readonly":
        pattern.readonly = object()
    elif case == "focus":
        monkeypatch.setattr(tr, "_focus_matches", lambda old: False)
    elif case == "changed_text":
        element.raw.SetFocus = lambda: setattr(pattern, "text", "user changed this")
    elif case == "changed_selection":
        monkeypatch.setattr(Range, "Select", lambda self: setattr(self.provider, "selected", [Range(self.provider, 0, 1)]))
    with pytest.raises(el.ElementError):
        tr.act(None, element, "replace_text", "new", spec={"text": "two"})
    assert not sent


def test_failed_readback_does_not_retry(provider, monkeypatch):
    element, pattern, sent = provider
    def miss(text, **kwargs):
        sent.append(text)
    monkeypatch.setattr(tr.native_input, "type_text", miss)
    assert tr.act(None, element, "replace_text", "new", spec={"text": "two"}).verified is False
    assert sent == ["new"]


def test_empty_selection_and_tabs_are_rejected_before_input(provider):
    element, pattern, sent = provider
    with pytest.raises(el.AmbiguousTarget):
        tr.act(None, element, "replace_text", "new")
    with pytest.raises(ValueError):
        tr.act(None, element, "append_text", "unsafe\ttab")
    assert not sent


def test_utf16_read_budget_never_returns_half_an_emoji(provider):
    element, pattern, sent = provider
    pattern.text = "🌍🌍"
    result = tr.read(element, max_chars=3)
    assert result["text"] == "🌍" and result["text_truncated"] is True


@pytest.mark.parametrize("spec", [{}, {"text": "x", "unit": "word"}, {"text": "x", "occurrence": 0},
    {"unit": "word", "index": True}, {"text": ""}, {"unit": "document", "index": 2}, {"text": "x", "unknown": 1}])
def test_invalid_range_contracts_fail(spec):
    with pytest.raises(ValueError):
        TextRangeSpec.model_validate(spec)
