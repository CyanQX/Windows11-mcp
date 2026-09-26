"""Desktop thread, telemetry opt-in, tool wiring and safety rails of the upgraded tools."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from windows_mcp import runtime
from windows_mcp.infrastructure import analytics as analytics_module
from windows_mcp.infrastructure import telemetry_enabled, with_analytics
from windows_mcp.tree.views import BoundingBox, Center, ScrollElementNode, SemanticNode, TreeElementNode, TreeState


class FakeMCP:
    def __init__(self):
        self.tools = {}
        self.options = {}

    def tool(self, *, name, **kwargs):
        self.options[name] = kwargs

        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


class TestDesktopThread:
    def test_desktop_tools_all_run_on_one_dedicated_thread(self):
        seen = set()

        @with_analytics(None, "probe")
        def probe():
            seen.add(threading.get_ident())
            return runtime.on_desktop_thread()

        results = [asyncio.run(probe()) for _ in range(5)]
        assert results == [True] * 5
        assert len(seen) == 1 and threading.get_ident() not in seen

    def test_concurrent_desktop_calls_never_overlap(self):
        active, overlaps = [0], []

        @with_analytics(None, "probe")
        def slow():
            active[0] += 1
            overlaps.append(active[0])
            time.sleep(0.02)
            active[0] -= 1

        async def many():
            await asyncio.gather(*(slow() for _ in range(6)))

        asyncio.run(many())
        assert max(overlaps) == 1  # input from two tool calls can never interleave

    def test_background_tools_do_not_block_the_desktop_thread(self):
        @with_analytics(None, "probe", offload="background")
        def where():
            return runtime.on_desktop_thread()

        assert asyncio.run(where()) is False

    def test_call_on_desktop_is_reentrant(self):
        assert runtime.call_on_desktop(lambda: runtime.call_on_desktop(runtime.on_desktop_thread)) is True


class TestTelemetry:
    @pytest.mark.parametrize(("value", "enabled"), [(None, False), ("", False), ("false", False), ("true", True), ("1", True), ("ON", True)])
    def test_telemetry_is_opt_in(self, monkeypatch, value, enabled):
        if value is None:
            monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)
        else:
            monkeypatch.setenv("ANONYMIZED_TELEMETRY", value)
        assert telemetry_enabled() is enabled

    def test_posthog_is_not_imported_at_module_load(self):
        assert "posthog" not in analytics_module.__dict__


class TestProcessProtection:
    def test_critical_processes_cannot_be_killed_by_name(self):
        from windows_mcp.process import kill_process

        with pytest.raises(PermissionError, match="critical Windows process"):
            kill_process(name="csrss.exe")

    def test_the_server_cannot_kill_itself(self):
        import os

        from windows_mcp.process import kill_process

        with pytest.raises(PermissionError, match="this Windows-MCP server"):
            kill_process(pid=os.getpid())


class TestAppModes:
    def _tools(self, desktop):
        from windows_mcp.tools import app

        mcp = FakeMCP()
        app.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
        return mcp.tools

    def test_window_commands_go_to_the_verified_window_command(self):
        calls = []

        class Desktop:
            def window_command(self, mode, name=None, handle=None):
                calls.append((mode, name, handle))
                return "ok"

        tools = self._tools(Desktop())
        assert asyncio.run(tools["App"](mode="minimize", name="Notepad")) == "ok"
        assert asyncio.run(tools["App"](mode="close", handle=0x1234)) == "ok"
        assert calls == [("minimize", "Notepad", None), ("close", None, 0x1234)]

    def test_window_commands_need_a_target(self):
        with pytest.raises(ValueError, match="needs name"):
            asyncio.run(self._tools(object())["App"](mode="maximize"))

    def test_switch_by_handle_is_passed_through(self):
        calls = []

        class Desktop:
            def app(self, mode, name, loc, size, handle=None):
                calls.append((mode, handle))
                return "switched"

        asyncio.run(self._tools(Desktop())["App"](mode="switch", handle=77))
        assert calls == [("switch", 77)]


class TestInputToolValidation:
    def _tools(self, desktop):
        from windows_mcp.tools import input as input_tools

        mcp = FakeMCP()
        input_tools.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
        return mcp.tools

    def test_shortcut_rejects_unknown_keys_before_sending_anything(self):
        class Desktop:
            def shortcut(self, *args, **kwargs):
                pytest.fail("no key may be sent for an invalid combination")

        with pytest.raises(ValueError, match="unknown key"):
            asyncio.run(self._tools(Desktop())["Shortcut"](shortcut="ctrl+banana"))

    def test_type_method_value_requires_label_and_clear(self):
        class Desktop:
            desktop_state = object()

        with pytest.raises(ValueError, match="method='value' needs a label"):
            asyncio.run(self._tools(Desktop())["Type"](text="x", loc=[1, 2], method="value"))

    @pytest.mark.parametrize("clicks", [-1, 4, True])
    def test_click_rejects_invalid_click_counts(self, clicks):
        with pytest.raises(ValueError, match="clicks must be"):
            asyncio.run(self._tools(object())["Click"](loc=[1, 2], clicks=clicks))


class TestSnapshotLabels:
    def test_semantic_tree_shows_label_numbers_for_interactive_and_scrollable_nodes(self):
        box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
        ok = TreeElementNode(bounding_box=box, center=Center(5, 5), name="OK", control_type="Button", window_name="App")
        cancel = TreeElementNode(bounding_box=box, center=Center(8, 5), name="Cancel", control_type="Button", window_name="App")
        pane = ScrollElementNode(name="List", control_type="Pane", window_name="App", bounding_box=box, center=Center(5, 9))
        root = SemanticNode(control_type="Desktop", element_type="desktop", name="Desktop")
        window = SemanticNode(control_type="Window", element_type="window", name="App", window_name="App")
        root.add_child(window)
        for node, kind in ((ok, "interactive"), (cancel, "interactive"), (pane, "scrollable")):
            window.add_child(
                SemanticNode(control_type=node.control_type, element_type=kind, name=node.name,
                             window_name="App", center=node.center, bounding_box=box)
            )  # fmt: skip
        text = TreeState(interactive_nodes=[ok, cancel], scrollable_nodes=[pane], semantic_tree_root=root).semantic_tree_to_string()
        assert '#0 (5,5) button "OK"' in text
        assert '#1 (8,5) button "Cancel"' in text
        assert '#2 (5,9) pane "List"' in text


def test_text_only_snapshot_is_plain_text_not_a_json_encoded_list():
    from windows_mcp.desktop.views import DesktopState
    from windows_mcp.tools._snapshot_helpers import build_snapshot_response

    state = DesktopState(active_desktop={"name": "D"}, all_desktops=[], active_window=None, windows=[], tree_state=TreeState())
    capture = {
        "desktop_state": state, "interactive_elements": "", "scrollable_elements": "", "semantic_tree": "desktop",
        "windows": "", "active_window": "", "active_desktop": "", "all_desktops": "", "screenshot_bytes": None,
    }  # fmt: skip
    response = build_snapshot_response(capture, include_ui_details=True)
    assert isinstance(response, str) and "\n" in response
    capture["screenshot_bytes"] = b"\x89PNG"
    with_image = build_snapshot_response(capture, include_ui_details=False)
    assert isinstance(with_image, list) and len(with_image) == 2


def test_word_nodes_are_opt_in(monkeypatch):
    from windows_mcp.tree.service import Tree

    tree = Tree.__new__(Tree)
    assert getattr(tree, "include_words", False) is False
    captured = {}

    def fake_nodes(self, windows_handles, active_window_flag, use_dom=False):
        captured["include_words"] = self.include_words
        return [], [], [], [], []

    monkeypatch.setattr(Tree, "get_window_wise_nodes", fake_nodes)
    tree.screen_box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    tree.get_state(None, [])
    assert captured["include_words"] is False
    tree.get_state(None, [], include_words=True)
    assert captured["include_words"] is True


def test_instructions_tell_the_agent_to_act_and_verify():
    from windows_mcp.__main__ import instructions

    for phrase in ("REAL Windows desktop", "OBSERVE -> ACT -> VERIFY", "Act(", "Find", "NOT verified", "foreground"):
        assert phrase in instructions
