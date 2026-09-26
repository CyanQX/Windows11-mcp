"""End-to-end proof that the tools operate a real application.

Every assertion is made against what the Win32 fixture application itself *received* (its own
event log): screen coordinates of mouse messages, WM_CHAR code units, control notifications.
"""

from __future__ import annotations

import re
import time

import pytest
import win32con
import win32gui

from tests.desktop.guard import assert_point_owned
from windows_mcp.desktop import native_input


def _find_ref(output: str, label_fragment: str) -> str:
    for line in output.splitlines():
        if label_fragment in line:
            match = re.match(r"\s*(e\d+)\s", line)
            if match:
                return match.group(1)
    raise AssertionError(f"{label_fragment!r} not found in Find output:\n{output}")


def _units(text: str) -> list[int]:
    return native_input.utf16_units(text)


def test_click_lands_on_the_exact_pixel(app, call):
    left, top, right, bottom = app.canvas
    targets = [(left + 12, top + 9), ((left + right) // 2, (top + bottom) // 2), (right - 17, bottom - 13)]
    since = app.mark()
    for x, y in targets:
        assert_point_owned(x, y, {app.pid})
        output = call("Click", loc=[x, y])
        assert f"at ({x},{y})" in output
    time.sleep(0.2)
    downs = [(e["x"], e["y"]) for e in app.events(since) if e.get("kind") == "left_down"]
    assert downs == targets


def test_right_middle_and_double_clicks_are_real_button_events(app, call):
    x, y = app.center("canvas")
    assert_point_owned(x, y, {app.pid})
    since = app.mark()
    call("Click", loc=[x, y], button="right")
    call("Click", loc=[x, y + 20], button="middle")
    call("Click", loc=[x - 30, y], clicks=2)
    time.sleep(0.3)
    kinds = [e["kind"] for e in app.events(since) if e["ev"] == "mouse"]
    assert "right_down" in kinds
    assert "middle_down" in kinds
    assert "left_double" in kinds  # Windows recognised a genuine double click


def test_type_sends_exact_unicode_including_cjk_emoji_and_control_keys(app, call):
    x, y = app.center("canvas")
    assert_point_owned(x, y, {app.pid})
    text = "Hi 世界 🌍 é{x}(y)+%^~\tZ\nend"
    since = app.mark()
    output = call("Type", text=text, loc=[x, y])
    assert "Typed" in output
    time.sleep(0.3)
    received = [e["code"] for e in app.events(since) if e["ev"] == "char"]
    expected = []
    for char in text:
        if char == "\t":
            expected.append(9)
        elif char == "\n":
            expected.append(13)
        else:
            expected += _units(char)
    assert received == expected


def test_type_into_native_edit_is_read_back_and_verified(app, call):
    x, y = app.center("name")
    assert_point_owned(x, y, {app.pid})
    since = app.mark()
    output = call("Type", text="张三 Zhang", loc=[x, y], clear=True)
    assert "Verified" in output, output
    values = [e["value"] for e in app.events(since) if e["ev"] == "text" and e["control"] == "name"]
    assert values and values[-1] == "张三 Zhang"
    output = call("Type", text="李四", loc=[x, y], clear=True)
    assert "Verified" in output, output
    values = [e["value"] for e in app.events(since) if e["ev"] == "text" and e["control"] == "name"]
    assert values[-1] == "李四"


def test_find_returns_live_controls_with_capabilities(app, call):
    output = call("Find", window=app.title)
    assert 'Button "Save"' in output
    assert "[invoke]" in output
    assert 'CheckBox "Enable feature"' in output
    assert "toggle=off" in output
    filtered = call("Find", name="save", window=app.title)
    assert filtered.splitlines()[1].split()[1:3] == ["Button", '"Save"']


def test_act_click_on_a_button_is_a_real_click_the_app_handles(app, call):
    ref = _find_ref(call("Find", name="Save", control_type="button", window=app.title), 'Button "Save"')
    since = app.mark()
    output = call("Act", target=ref, action="click")
    assert "real left click" in output
    clicks = app.wait_for(lambda e: e.get("control") == "save", since)
    assert [e["count"] for e in clicks] == [1]


def test_act_set_value_on_native_edit_verifies_the_value(app, call):
    ref = _find_ref(call("Find", name="Name", control_type="edit", window=app.title), 'Edit "Name"')
    since = app.mark()
    output = call("Act", target=ref, action="set_value", value="Grace Hopper")
    assert "Verified" in output, output
    values = [e["value"] for e in app.events(since) if e["ev"] == "text" and e["control"] == "name"]
    assert values[-1] == "Grace Hopper"


def test_act_type_appends_with_the_keyboard(app, call):
    ref = _find_ref(call("Find", name="Notes", control_type="edit", window=app.title), 'Edit "Notes"')
    call("Act", target=ref, action="set_value", value="line one")
    output = call("Act", target=ref, action="type", value=" + more")
    assert "real keyboard typing" in output
    assert "Verified" in output, output


def test_act_toggle_clicks_until_the_requested_state(app, call):
    since = app.mark()
    output = call("Act", target="Enable feature", action="toggle", value="on", window=app.title)
    assert "real left click" in output and "Verified: state off -> on" in output, output
    output = call("Act", target="Enable feature", action="toggle", value="on", window=app.title)
    assert "Verified: already on" in output  # idempotent: no second click
    checks = [e["checked"] for e in app.events(since) if e["ev"] == "check" and e["control"] == "enable"]
    assert checks == [True]  # the application saw exactly one real BN_CLICKED


def test_act_select_opens_the_combo_box_and_clicks_the_item(app, call):
    ref = _find_ref(call("Find", control_type="combobox", window=app.title), "ComboBox")
    since = app.mark()
    output = call("Act", target=ref, action="select", value="Cherry")
    assert 'real click on item "Cherry"' in output and "Verified" in output, output
    selects = app.wait_for(lambda e: e.get("control") == "combo", since)
    assert selects and selects[-1]["index"] == 2  # the app's own CBN_SELCHANGE handler ran


def test_act_select_scrolls_an_offscreen_list_item_into_view(app, call):
    ref = _find_ref(call("Find", control_type="list", window=app.title), "List")
    since = app.mark()
    output = call("Act", target=ref, action="select", value="Item 45")
    assert "Verified" in output, output
    selects = app.wait_for(lambda e: e.get("control") == "list", since)
    assert selects and selects[-1]["index"] == 44


def test_via_uia_changes_values_without_input_and_still_notifies_the_app(app, call):
    combo = _find_ref(call("Find", control_type="combobox", window=app.title), "ComboBox")
    since = app.mark()
    output = call("Act", target=combo, action="select", value="Durian", via="uia")
    assert 'UIA Select of item "Durian"' in output and "Verified" in output, output
    selects = app.wait_for(lambda e: e.get("control") == "combo", since)
    assert selects and selects[-1]["index"] == 3
    name = _find_ref(call("Find", name="Name", control_type="edit", window=app.title), 'Edit "Name"')
    output = call("Act", target=name, action="set_value", value="via pattern", via="uia")
    assert "UIA SetValue" in output and "Verified" in output, output


def test_act_set_range_moves_the_slider_and_notifies_the_app(app, call):
    ref = _find_ref(call("Find", control_type="slider", window=app.title), "Slider")
    since = app.mark()
    output = call("Act", target=ref, action="set_range", value="65")
    assert "Verified: value now 65" in output, output
    slider = app.wait_for(lambda e: e["ev"] == "slider", since)
    assert slider and slider[-1]["value"] == 65
    with pytest.raises(ValueError, match="outside the allowed range"):
        call("Act", target=ref, action="set_range", value="500")


def test_act_reports_the_modal_dialog_it_opened_and_can_close_it(app, call):
    since = app.mark()
    output = call("Act", target="Open dialog", action="click", window=app.title)
    assert 'window opened: "Fixture dialog"' in output, output
    # The OK button is localised ("确定" on Chinese Windows), so find it by type.
    ok = _find_ref(call("Find", control_type="button", window="Fixture dialog"), "Button")
    output = call("Act", target=ok, action="click")
    assert 'window closed: "Fixture dialog"' in output, output
    assert app.wait_for(lambda e: e["ev"] == "dialog_closed", since)


def test_snapshot_label_is_relocated_after_the_window_moved(app, call):
    snapshot = call("Snapshot", window=app.title)
    text = snapshot[0] if isinstance(snapshot, list) else snapshot
    match = re.search(r'#(\d+) \(\d+,\d+\) [^"\n]* "Save"', text)
    assert match, text
    label = int(match.group(1))
    left, top, right, bottom = app.window
    win32gui.SetWindowPos(
        app.hwnd, win32con.HWND_TOPMOST, left + 160, top + 90, right - left, bottom - top, win32con.SWP_NOACTIVATE
    )
    time.sleep(0.3)
    since = app.mark()
    output = call("Click", label=label)
    assert 'Button "Save"' in output, output
    assert app.wait_for(lambda e: e.get("control") == "save", since), "the moved button was not clicked"


def test_act_brings_a_covered_window_forward_before_clicking(make_app, call):
    target = make_app()
    ref = _find_ref(call("Find", name="Save", control_type="button", window=target.title), 'Button "Save"')
    cover = make_app()  # a second topmost window, placed exactly over the first one
    left, top, right, bottom = target.window
    win32gui.SetWindowPos(cover.hwnd, win32con.HWND_TOPMOST, left, top, right - left, bottom - top, 0)
    time.sleep(0.3)
    since_target, since_cover = target.mark(), cover.mark()
    output = call("Act", target=ref, action="click")
    assert "brought its window to the front" in output, output
    assert target.wait_for(lambda e: e.get("control") == "save", since_target)
    assert not [e for e in cover.events(since_cover) if e.get("control") == "save"]


def test_scroll_emits_real_wheel_deltas_and_reports_position(app, call):
    x, y = app.center("canvas")
    assert_point_owned(x, y, {app.pid})
    since = app.mark()
    call("Scroll", loc=[x, y], direction="down", wheel_times=2)
    call("Scroll", loc=[x, y], type="horizontal", direction="right", wheel_times=1)
    time.sleep(0.2)
    wheels = [(e["axis"], e["delta"]) for e in app.events(since) if e["ev"] == "wheel"]
    assert wheels == [("v", -120), ("v", -120), ("h", 120)]
    lx, ly = app.center("list")
    assert_point_owned(lx, ly, {app.pid})
    output = call("Scroll", loc=[lx, ly], direction="down", wheel_times=3)
    assert re.search(r"scrolled 0\.0% -> \d+\.\d%", output), output


def test_shortcut_is_scoped_to_the_window_and_atomic(app, call):
    x, y = app.center("name")
    assert_point_owned(x, y, {app.pid})
    call("Type", text="select me", loc=[x, y], clear=True)
    since = app.mark()
    output = call("Shortcut", shortcut="ctrl+a", window=app.title)
    assert f'in "{app.title}"' in output
    call("Shortcut", shortcut="backspace", window=app.title)
    assert native_input.modifiers_down() == []  # nothing left pressed
    values = [e["value"] for e in app.wait_for(lambda e: e.get("control") == "name", since)]
    assert values and values[-1] == ""  # ctrl+a selected everything, backspace removed it


def test_app_window_commands_are_verified(app, call):
    assert "(verified)" in call("App", mode="minimize", name=app.title)
    assert win32gui.IsIconic(app.hwnd)
    assert "(verified)" in call("App", mode="restore", name=app.title)
    since = app.mark()
    output = call("App", mode="close", name=app.title)
    assert "the window is gone" in output, output
    assert app.wait_for(lambda e: e["ev"] == "closing", since)
