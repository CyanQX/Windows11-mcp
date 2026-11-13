"""Real process boundaries; all images synthetic and no native input is sent."""

import io
import queue
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image

from windows_mcp.desktop.coordination import DesktopBusyError, desktop_lease, status
from windows_mcp.engine.sessions import SessionState
from windows_mcp.runtime import call_on_desktop
from windows_mcp.vision import adapter
from windows_mcp.vision.capture_worker import CaptureWorker
from windows_mcp.vision.grounding import ScreenshotSpace


@pytest.fixture
def worker():
    instance = CaptureWorker()
    yield instance
    instance.close()


def test_native_capture_process_is_reused_and_shutdown_reaps_it(worker):
    first = worker.request({"operation": "probe"})
    process = worker.process
    assert "wgc" in first
    assert "wgc" in worker.request({"operation": "probe"})
    assert worker.process is process and worker.status()["starts"] == 1
    assert worker.status()["requests"] == 2
    worker.close()
    assert process.poll() is not None and not worker.status()["running"]


def test_native_provider_error_does_not_corrupt_protocol(worker):
    with pytest.raises(RuntimeError, match="provider failed"):
        worker.request({"operation": "capture", "backend": "invalid"})
    process = worker.process
    assert "wgc" in worker.request({"operation": "probe"})
    assert worker.process is process


def test_capture_timeout_reaps_job_and_next_request_starts_fresh(worker):
    with pytest.raises(subprocess.TimeoutExpired):
        worker.request({"operation": "probe"}, timeout=.001)
    assert not worker.status()["running"] and worker.status()["timeouts"] == 1
    assert "wgc" in worker.request({"operation": "probe"})
    assert worker.status()["starts"] == 2


def test_crashed_capture_worker_is_replaced_only_on_a_new_request(worker):
    worker.request({"operation": "probe"})
    old = worker.process
    old.kill()
    old.wait(timeout=5)
    assert worker.status()["starts"] == 1
    worker.request({"operation": "probe"})
    assert worker.process.pid != old.pid and worker.status()["starts"] == 2


def test_capture_requests_are_serialized(worker):
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: worker.request({"operation": "probe"}), range(3)))
    assert all("wgc" in item for item in results)
    assert worker.status()["starts"] == 1 and worker.status()["requests"] == 3


@pytest.mark.parametrize("data", ["x" * 1_000_001, "not json\n", "", "{}"], ids=["oversized", "invalid", "eof", "unterminated"])
def test_capture_response_is_bounded(data):
    responses = queue.Queue(maxsize=1)
    CaptureWorker._read(io.StringIO(data), responses)
    assert isinstance(responses.get_nowait(), RuntimeError)


@pytest.fixture
def foreign_lease():
    from windows_mcp.desktop import coordination

    code = ("import sys; import windows_mcp.desktop.coordination as c; c.NAME=sys.argv[1]; "
            "from windows_mcp.desktop.coordination import desktop_lease; "
            "lease=desktop_lease(); lease.__enter__(); print('ready',flush=True); sys.stdin.read(1)")
    process = subprocess.Popen([sys.executable, "-c", code, coordination.NAME], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_other_process_blocks_entire_desktop_job_before_side_effect(foreign_lease, tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    assert status()["busy"]
    with pytest.raises(DesktopBusyError, match="DESKTOP_BUSY"):
        call_on_desktop(marker.write_text, "bad")
    assert not marker.exists()


def test_abandoned_lease_does_not_leave_desktop_locked(foreign_lease):
    foreign_lease.kill()
    foreign_lease.wait(timeout=5)
    assert call_on_desktop(lambda: "released") == "released"


def test_nested_desktop_job_lease_is_reentrant_and_exception_releases():
    with pytest.raises(ValueError):
        with desktop_lease(), desktop_lease():
            assert status()["owned_by_this_thread"]
            raise ValueError("leave")
    assert not status()["busy"]


@pytest.fixture
def patch(monkeypatch):
    image = Image.new("RGB", (200, 100), "blue")
    space = ScreenshotSpace(100, 200, (-80, 30, 80, 110), (80, 40), window_rect=(-100, 20, 100, 120))
    reference = adapter.VisualReference(image.crop((20, 10, 180, 90)).resize((80, 40), Image.Resampling.LANCZOS),
                                        "printwindow", (-100, 20, 100, 120))
    monkeypatch.setattr(adapter.native_bridge, "capture", lambda *args: image.copy())
    return space, reference, image


def test_visual_patch_mapping_accounts_for_roi_scaling_negative_screen(patch):
    space, reference, _ = patch
    adapter._revalidate_patch(space, reference, [40, 20])


def test_visual_change_outside_click_neighborhood_does_not_block(patch):
    space, reference, image = patch
    image.paste("red", (20, 10, 28, 18))
    adapter._revalidate_patch(space, reference, [60, 25])


def test_changed_visual_neighborhood_stops_before_click(patch):
    space, reference, image = patch
    image.paste("red", (80, 40, 120, 80))
    with pytest.raises(adapter.elements.StaleTarget, match="content near the point changed"):
        adapter._revalidate_patch(space, reference, [40, 20])


def test_visual_small_rendering_noise_is_tolerated(patch):
    space, reference, image = patch
    image.paste((0, 0, 254), (0, 0, 200, 100))
    adapter._revalidate_patch(space, reference, [40, 20])


async def test_unchanged_capture_skips_png_encoding(monkeypatch):
    session, image = SessionState(), Image.new("RGB", (200, 100), "blue")
    info = SimpleNamespace(handle=100, pid=200)
    monkeypatch.setattr(adapter, "_prepare", lambda *_: (info, (0, 0, 200, 100), (0, 0, 200, 100), 1.0))
    monkeypatch.setattr(adapter, "_recheck", lambda *_: None)
    monkeypatch.setattr(adapter.elements, "foreground_root", lambda: 100)
    monkeypatch.setattr(adapter.native_bridge, "capture", lambda *_: image.copy())
    first, _ = await adapter.capture_async(None, session, "100", "canvas", backend="printwindow")
    monkeypatch.setattr(Image.Image, "save", lambda *_a, **_k: pytest.fail("unchanged image encoded again"))
    second, data = await adapter.capture_async(None, session, "100", "canvas", backend="printwindow",
                                              previous_target=first["target"], only_if_changed=True)
    assert not second["image_included"] and data is None
