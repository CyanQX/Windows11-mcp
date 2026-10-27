"""Images are synthetic; no real window is read or clicked by these tests."""

import io
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from windows_mcp.engine.sessions import SessionState
from windows_mcp.vision import adapter, native_bridge, native_worker


@pytest.fixture
def visual(monkeypatch):
    session, image = SessionState(), Image.new("RGB", (200, 100), "blue")
    info = SimpleNamespace(handle=100, pid=200)
    monkeypatch.setattr(adapter, "_prepare", lambda *args: (info, (10, 20, 210, 120), (10, 20, 210, 120), 1.0))
    monkeypatch.setattr(adapter, "_recheck", lambda *args: None)
    monkeypatch.setattr(adapter.elements, "foreground_root", lambda: 999)
    monkeypatch.setattr(native_bridge, "capture", lambda *args: image.copy())
    return session, image


async def test_roi_downscale_and_pixel_mapping_preserve_full_window_lifetime(visual):
    session, _ = visual
    metadata, data = await adapter.capture_async(None, session, "100", "canvas", backend="wgc", region=[20, 10, 180, 90], max_width=80)
    assert metadata["image_size"] == [80, 40] and metadata["desktop_bounds"] == [30, 30, 190, 110]
    assert Image.open(io.BytesIO(data)).size == (80, 40)
    space = session.visuals[metadata["target"]][0]
    assert space.point(40, 20, live_handle=100, live_pid=200, live_rect=(10, 20, 210, 120)) == (110, 70)
    with pytest.raises(ValueError, match="TARGET_STALE"):
        space.point(1, 1, live_handle=100, live_pid=200, live_rect=(11, 20, 211, 120))


async def test_identical_capture_can_omit_image_and_pixel_change_is_only_a_hint(visual):
    session, image = visual
    first, _ = await adapter.capture_async(None, session, "100", "canvas", backend="wgc")
    second, data = await adapter.capture_async(None, session, "100", "canvas", backend="wgc", previous_target=first["target"], only_if_changed=True)
    assert second["changed"] is False and data is None
    image.putpixel((0, 0), (0, 0, 0))
    third, data = await adapter.capture_async(None, session, "100", "canvas", backend="wgc", previous_target=second["target"], only_if_changed=True)
    assert third["changed"] is True and data is not None
    assert "verification" not in third


async def test_native_failure_falls_back_without_mislabeling_backend(visual, monkeypatch):
    session, image = visual
    def failure(*args):
        raise RuntimeError("native unavailable")
    monkeypatch.setattr(native_bridge, "capture", failure)
    monkeypatch.setattr(adapter, "_desktop_image", lambda *args: image.copy())
    result, _ = await adapter.capture_async(None, session, "100", "canvas")
    assert result["capture_backend"] == "desktop" and len(result["diagnostics"]) == 3


async def test_all_backends_failing_does_not_return_failed_candidate_image(visual, monkeypatch):
    session, _ = visual
    monkeypatch.setattr(native_bridge, "capture", lambda *args: Image.new("RGB", (1, 1)))
    monkeypatch.setattr(adapter, "_desktop_image", lambda *args: (_ for _ in ()).throw(RuntimeError("blocked")))
    with pytest.raises(RuntimeError, match="No window capture"):
        await adapter.capture_async(None, session, "100", "canvas")


@pytest.mark.parametrize("region", [[-1, 0, 10, 10], [0, 0, 300, 300], [0, 0, 0, 0], [True, 0, 10, 10]])
async def test_out_of_bounds_region_is_rejected(visual, region):
    with pytest.raises(ValueError, match="region"):
        await adapter.capture_async(None, visual[0], "100", "canvas", backend="wgc", region=region)


async def test_other_client_cannot_reuse_comparison_image(visual):
    first, _ = await adapter.capture_async(None, visual[0], "100", "canvas", backend="wgc")
    with pytest.raises(adapter.elements.StaleTarget):
        await adapter.capture_async(None, SessionState(), "100", "canvas", backend="wgc", previous_target=first["target"])


async def test_visual_click_rejects_reused_process_id_before_input(visual, monkeypatch):
    session, _ = visual
    first, _ = await adapter.capture_async(None, session, "100", "canvas", backend="wgc")
    monkeypatch.setattr(adapter.elements, "window_info", lambda handle: SimpleNamespace(pid=200, handle=100))
    monkeypatch.setattr(adapter.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 2.0))
    monkeypatch.setattr(adapter.native_input, "click", lambda *args: pytest.fail("stale image dispatched input"))
    with pytest.raises(adapter.elements.StaleTarget, match="process lifetime"):
        adapter.click(session, first["target"], [10, 10])


async def test_capture_cancelled_during_native_call_is_not_registered(visual, monkeypatch):
    session, image = visual
    def capture(*args):
        session.cancel()
        return image.copy()
    monkeypatch.setattr(native_bridge, "capture", capture)
    with pytest.raises(RuntimeError, match="CANCELLED"):
        await adapter.capture_async(None, session, "100", "canvas", backend="wgc")
    assert session.visuals == {}


def test_helper_timeout_is_bounded_and_process_is_reaped(monkeypatch):
    processes, spawn = [], subprocess.Popen
    def tracked(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(native_bridge.subprocess, "Popen", tracked)
    with pytest.raises(subprocess.TimeoutExpired):
        native_bridge.request({"operation": "probe"}, timeout=.001)
    assert len(processes) == 1 and processes[0].poll() is not None


def test_job_assignment_failure_is_reported_and_process_reaped(monkeypatch):
    import pywintypes
    import win32job

    processes, spawn = [], subprocess.Popen
    def tracked(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process
    def reject(*args):
        raise pywintypes.error(5, "AssignProcessToJobObject", "denied")
    monkeypatch.setattr(native_bridge.subprocess, "Popen", tracked)
    monkeypatch.setattr(win32job, "AssignProcessToJobObject", reject)
    with pytest.raises(RuntimeError, match="process boundary"):
        native_bridge.request({"operation": "probe"})
    assert len(processes) == 1 and processes[0].poll() is not None


def test_missing_package_identity_is_an_explicit_ocr_limit(monkeypatch):
    monkeypatch.setattr(native_worker, "probe", lambda: {"windows_ocr": {"available": False, "reason": "package_identity_required"}})
    result = native_worker.ocr(Image.new("RGB", (20, 20)), "windows_ocr", None)
    assert result["available"] is False and result["reason"] == "package_identity_required"


def test_tesseract_words_remain_in_image_pixels(monkeypatch):
    import sys
    monkeypatch.setattr(native_worker, "probe", lambda: {"tesseract": {"available": True}})
    fake = SimpleNamespace(get_languages=lambda: ["eng"], Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda *a, **k: {"text": ["", "Fixture"], "conf": [-1, 96], "left": [0, 5], "top": [0, 7], "width": [0, 20], "height": [0, 8]})
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    result = native_worker.ocr(Image.new("RGB", (100, 100)), "tesseract", "eng")
    assert result["available"] and result["words"][0]["bounds"] == [5, 7, 25, 15]
    assert result["coordinate_space"] == "image_pixels"
    assert native_worker.ocr(Image.new("RGB", (10, 10)), "tesseract", "missing")["reason"] == "ocr_language_unavailable"


@pytest.mark.parametrize("fails", [False, True])
async def test_windows_ocr_projection_contract_and_cleanup(monkeypatch, fails):
    """Exercise our projection contract; this is not real Windows OCR qualification."""
    import sys

    closed, written = [], []
    bitmap = SimpleNamespace(close=lambda: closed.append("bitmap"))

    class Writer:
        def write_bytes(self, data):
            written.append(data)

        def detach_buffer(self):
            return b"buffer"

        def close(self):
            closed.append("writer")

    async def recognize(bitmap):
        if fails:
            raise RuntimeError("provider failed")
        word = SimpleNamespace(text="Fixture", bounding_rect=SimpleNamespace(x=5, y=7, width=20, height=8))
        return SimpleNamespace(lines=[SimpleNamespace(words=[word])], text_angle=None)

    engine = SimpleNamespace(recognize_async=recognize, recognizer_language=SimpleNamespace(language_tag="en-US"))
    fake_engine = SimpleNamespace(available_recognizer_languages=[engine.recognizer_language], max_image_dimension=1000,
        try_create_from_language=lambda language: engine, try_create_from_user_profile_languages=lambda: engine)
    fake_bitmap = SimpleNamespace(create_copy_from_buffer=lambda *args: bitmap)
    monkeypatch.setattr(native_worker, "package_identity", lambda: "fixture.package")
    modules = {
        "winrt.windows.globalization": SimpleNamespace(Language=lambda value: value),
        "winrt.windows.graphics.imaging": SimpleNamespace(SoftwareBitmap=fake_bitmap,
            BitmapAlphaMode=SimpleNamespace(IGNORE=1), BitmapPixelFormat=SimpleNamespace(BGRA8=2)),
        "winrt.windows.media.ocr": SimpleNamespace(OcrEngine=fake_engine),
        "winrt.windows.storage.streams": SimpleNamespace(DataWriter=Writer),
    }
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    image = Image.new("RGB", (40, 40), (1, 2, 3))
    if fails:
        with pytest.raises(RuntimeError, match="provider failed"):
            await native_worker.windows_ocr(image, "en-US")
    else:
        result = await native_worker.windows_ocr(image, "en-US")
        assert result["words"][0]["bounds"] == [5, 7, 25, 15]
        assert result["language"] == "en-US" and result["coordinate_space"] == "image_pixels"
    assert written[0][:4] == bytes((3, 2, 1, 255))
    assert closed == ["bitmap", "writer"]
