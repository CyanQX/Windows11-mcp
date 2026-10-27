"""Native HWND capture against dedicated windows; user applications are not captured."""

import json

import pytest
import win32con
import win32gui

from windows_mcp.desktop import elements
from windows_mcp.runtime import call_on_desktop


def test_real_wgc_observes_covered_window_but_click_requires_foreground(make_app, call):
    pytest.importorskip("windows_capture")
    behind, covering = make_app(), make_app()
    win32gui.SetWindowPos(covering.hwnd, win32con.HWND_TOPMOST, 100, 100, 800, 640, 0)
    assert call_on_desktop(elements.foreground_root) != behind.hwnd
    result = call("Visual", window=str(behind.hwnd), fallback_reason="canvas", backend="wgc")
    metadata = json.loads(result[0])
    assert metadata["capture_backend"] == "wgc" and metadata["background_observation"]
    assert metadata["image_size"][0] > 100 and metadata["image_included"]
    action = call("Execute", target=metadata["target"], mode="visual", action="click", value="[20,20]")
    assert action["status"] == "FOCUS_MISMATCH" and not action["input_dispatched"]


def test_real_printwindow_roi_and_unchanged_image_suppression(app, call):
    options = dict(window=str(app.hwnd), fallback_reason="canvas", backend="printwindow", region=[410, 60, 510, 160], max_width=100)
    first = call("Visual", **options)
    metadata = json.loads(first[0])
    assert metadata["image_size"] == [100, 100]
    second = call("Visual", **options, previous_target=metadata["target"], only_if_changed=True, ocr=True)
    newer = json.loads(second[0])
    assert newer["changed"] is False and len(second) == 1
    assert "available" in newer["ocr"]


@pytest.mark.parametrize("profile,count", [("full", 31), ("ui-core", 12), ("browser", 5)])
async def test_real_stdio_health_checks_complete_profile_contract(tmp_path, profile, count):
    """Launch actual source service in isolated home; initialize/list/Session only."""
    import sys
    from pathlib import Path

    from installer.health import HealthChecker
    from windows_mcp.infrastructure.catalog import export_manifest

    manifest = await export_manifest({}, profile)
    manifest_path = tmp_path / "static.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    checker = HealthChecker(manifest, 60, home=tmp_path / "isolated-home",
        arguments=["-m", "windows_mcp", "serve", "--profile", profile, "--static-manifest", str(manifest_path)])
    result = await checker.probe(Path(sys.executable))
    assert result.ok, result.to_dict()
    assert result.tool_count == count and result.session_status == "READY"
    assert result.schema_checked and result.result_checked
    assert isinstance(result.native_components, dict)
