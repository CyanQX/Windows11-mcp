"""native_input: event construction is checked without sending anything (SendInput is stubbed)."""

from __future__ import annotations

import pytest

from windows_mcp.desktop import native_input as ni


@pytest.fixture
def sent(monkeypatch):
    batches: list[list] = []
    monkeypatch.setattr(ni, "_send", lambda inputs: batches.append(list(inputs)))
    monkeypatch.setattr(ni.time, "sleep", lambda seconds: None)
    return batches


def _keys(batch):
    return [
        (event.u.ki.wVk, bool(event.u.ki.dwFlags & ni.KEYEVENTF_KEYUP), bool(event.u.ki.dwFlags & ni.KEYEVENTF_EXTENDEDKEY))
        for event in batch
    ]


class TestTextEncoding:
    def test_utf16_units_split_emoji_into_a_surrogate_pair(self):
        assert ni.utf16_units("A") == [0x41]
        assert ni.utf16_units("世") == [0x4E16]
        assert ni.utf16_units("🌍") == [0xD83C, 0xDF0D]

    def test_text_events_are_unicode_units_with_enter_and_tab_as_keys(self):
        events = ni.text_events("a🌍\t\r\nb")
        unicode_units = [e.u.ki.wScan for e in events if e.u.ki.dwFlags & ni.KEYEVENTF_UNICODE and not e.u.ki.dwFlags & ni.KEYEVENTF_KEYUP]
        assert unicode_units == [ord("a"), 0xD83C, 0xDF0D, ord("b")]
        vks = [e.u.ki.wVk for e in events if not e.u.ki.dwFlags & ni.KEYEVENTF_UNICODE and not e.u.ki.dwFlags & ni.KEYEVENTF_KEYUP]
        assert vks == [ni.VK_TAB, ni.VK_RETURN]  # \r\n collapses to ONE Enter

    def test_every_event_is_tagged_as_ours(self):
        assert all(e.u.ki.dwExtraInfo == ni.EXTRA_INFO for e in ni.text_events("xy\n"))

    def test_type_text_sends_in_chunks(self, sent):
        assert ni.type_text("x" * 70, chunk_chars=32) == 70
        assert [len(batch) for batch in sent] == [64, 64, 12]  # down+up per character


class TestChords:
    def test_modifiers_wrap_the_key_and_are_released_in_reverse(self):
        chord = ni.parse_chord("ctrl+shift+s")
        assert chord.modifiers == (ni.VK_CONTROL, ni.VK_SHIFT)
        assert chord.keys == (ord("S"),)
        assert _keys(ni.chord_events(chord)) == [
            (ni.VK_CONTROL, False, False),
            (ni.VK_SHIFT, False, False),
            (ord("S"), False, False),
            (ord("S"), True, False),
            (ni.VK_SHIFT, True, False),
            (ni.VK_CONTROL, True, False),
        ]

    @pytest.mark.parametrize(
        ("spec", "vk", "extended"),
        [("delete", 0x2E, True), ("up", 0x26, True), ("home", 0x24, True), ("win", 0x5B, True),
         ("enter", 0x0D, False), ("a", 0x41, False), ("f5", 0x74, False), ("tab", 0x09, False)],
    )  # fmt: skip
    def test_only_real_extended_keys_carry_the_extended_flag(self, spec, vk, extended):
        events = _keys(ni.chord_events(ni.parse_chord(spec)))
        assert events == [(vk, False, extended), (vk, True, extended)]

    def test_lone_modifier_is_pressed_as_a_key(self):
        chord = ni.parse_chord("win")
        assert chord.modifiers == () and chord.keys == (ni.VK_LWIN,)

    def test_ctrl_plus_plus_means_the_plus_key(self):
        chord = ni.parse_chord("ctrl++")
        assert chord.modifiers[0] == ni.VK_CONTROL
        assert len(chord.keys) == 1

    def test_names_are_case_and_spelling_tolerant(self):
        assert ni.parse_chord("Ctrl+PageDown").keys == (0x22,)
        assert ni.parse_chord("alt+F4").keys == (0x73,)
        assert ni.parse_chord("page_up").keys == (0x21,)

    def test_sequences_are_split_on_spaces(self):
        chords = ni.parse_sequence("ctrl+k ctrl+s")
        assert [c.keys for c in chords] == [(ord("K"),), (ord("S"),)]

    @pytest.mark.parametrize("spec", ["", "ctrl+", "+ctrl", "ctrl+nosuchkey"])
    def test_malformed_combinations_are_rejected_before_any_input(self, spec, sent):
        with pytest.raises(ValueError):
            ni.press_keys(spec)
        assert sent == []

    def test_each_chord_is_one_atomic_batch(self, sent):
        ni.press_keys("ctrl+c", repeat=3)
        assert len(sent) == 3 and all(len(batch) == 4 for batch in sent)

    def test_hold_releases_even_when_the_body_fails(self, sent):
        with pytest.raises(RuntimeError), ni.hold("ctrl"):
            raise RuntimeError("click failed")
        assert _keys(sent[-1]) == [(ni.VK_CONTROL, True, False)]


class TestMouse:
    def test_absolute_coordinates_cover_the_virtual_desktop_including_negative_monitors(self):
        screen = ni.VirtualScreen(left=-1920, top=-200, width=4480, height=1640)
        assert ni.to_absolute(-1920, -200, screen) == (0, 0)
        assert ni.to_absolute(2559, 1439, screen) == (65535, 65535)
        x, y = ni.to_absolute(0, 0, screen)
        assert x == round(1920 * 65535 / 4479) and y == round(200 * 65535 / 1639)

    def test_click_rejects_bad_buttons_and_counts(self):
        with pytest.raises(ValueError):
            ni.click(1, 1, button="fourth")
        with pytest.raises(ValueError):
            ni.click(1, 1, count=4)

    def test_wheel_sends_one_detent_per_notch_with_the_right_sign(self, sent):
        ni.wheel(-2)
        ni.wheel(1, horizontal=True)
        flags = [(batch[0].u.mi.dwFlags, ctypes_signed(batch[0].u.mi.mouseData)) for batch in sent]
        assert flags == [(ni.MOUSEEVENTF_WHEEL, -120), (ni.MOUSEEVENTF_WHEEL, -120), (ni.MOUSEEVENTF_HWHEEL, 120)]

    def test_drag_always_releases_the_button(self, monkeypatch, sent):
        monkeypatch.setattr(ni, "move_to", lambda *a, **k: (0, 0))
        calls = []

        def failing_move(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("cursor blocked")
            return (0, 0)

        monkeypatch.setattr(ni, "move_to", failing_move)
        with pytest.raises(RuntimeError):
            ni.drag((1, 1), (50, 50))
        assert sent[-1][0].u.mi.dwFlags == ni.MOUSEEVENTF_LEFTUP


def ctypes_signed(value: int) -> int:
    return value - (1 << 32) if value & 0x80000000 else value


def test_blocked_input_raises_instead_of_pretending(monkeypatch):
    monkeypatch.setattr(ni._user32, "SendInput", lambda count, array, size: 0)
    with pytest.raises(ni.InputBlockedError, match="administrator"):
        ni._send(ni.text_events("x"))
