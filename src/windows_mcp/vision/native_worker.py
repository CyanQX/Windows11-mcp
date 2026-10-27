"""Private, one-shot native capture/OCR helper. No UIA, input, policy changes or shell API."""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import json
import shutil
import subprocess
import sys
import threading

from PIL import Image

MAX_PIXELS = 16000000


def package_identity() -> str | None:
    length = ctypes.c_uint32()
    api = ctypes.windll.kernel32.GetCurrentPackageFullName
    if api(ctypes.byref(length), None) != 122:
        return None
    buffer = ctypes.create_unicode_buffer(length.value)
    return buffer.value if api(ctypes.byref(length), buffer) == 0 else None


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def probe() -> dict:
    identity = package_identity()
    winrt_ocr = all(_installed(name) for name in ("winrt.windows.media.ocr", "winrt.windows.graphics.imaging",
                    "winrt.windows.storage.streams", "winrt.windows.globalization"))
    executable = shutil.which("tesseract")
    languages, languages_checked = [], False
    if executable:
        try:
            result = subprocess.run([executable, "--list-langs"], capture_output=True, text=True,
                                    timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
            languages = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()][:100]
            languages_checked = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    windows_languages = []
    if winrt_ocr and identity:
        try:
            from winrt.windows.media.ocr import OcrEngine

            windows_languages = [item.language_tag for item in OcrEngine.available_recognizer_languages]
        except (OSError, RuntimeError):
            pass
    return {"package_identity": bool(identity),
        "wgc": {"available": _installed("windows_capture"), "provider": "Windows.Graphics.Capture"},
        "windows_ocr": {"available": winrt_ocr and bool(identity), "dependencies_installed": winrt_ocr,
                        "languages": windows_languages,
                        "reason": "ready" if winrt_ocr and identity else "package_identity_required" if winrt_ocr else "optional_dependencies_missing"},
        "tesseract": {"available": bool(executable) and _installed("pytesseract"),
                      "engine_installed": bool(executable), "languages": languages, "languages_checked": languages_checked}}


def wgc(hwnd: int) -> Image.Image:
    from windows_capture import WindowsCapture

    capture = WindowsCapture(window_hwnd=hwnd, cursor_capture=False)
    done, frames, errors = threading.Event(), [], []

    @capture.event
    def on_frame_arrived(frame, capture_control):
        try:
            if frame.width <= 0 or frame.height <= 0 or frame.width * frame.height > MAX_PIXELS:
                raise ValueError("Captured frame exceeds image bounds")
            # Copy while the native mapped surface is alive, before stopping capture.
            frames.append(Image.fromarray(frame.frame_buffer[:, :, [2, 1, 0]].copy()))
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            capture_control.stop()
            done.set()

    @capture.event
    def on_closed():
        done.set()

    control = capture.start_free_threaded()
    try:
        if not done.wait(5):
            raise TimeoutError("WGC did not deliver a frame")
        if not frames:
            raise RuntimeError("WGC closed without a usable frame: " + ",".join(errors))
        return frames[0]
    finally:
        control.stop()


def print_window(hwnd: int) -> Image.Image:
    import win32gui
    import win32ui

    rect = win32gui.GetWindowRect(hwnd)
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError("Window image exceeds bounds")
    window_dc = win32gui.GetWindowDC(hwnd)
    source, memory, bitmap, previous = None, None, None, None
    try:
        source = win32ui.CreateDCFromHandle(window_dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        previous = memory.SelectObject(bitmap)
        if not ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2):
            raise RuntimeError("PrintWindow did not render the window")
        data = bitmap.GetBitmapBits(True)
        return Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1).copy()
    finally:
        if memory is not None:
            if previous is not None:
                memory.SelectObject(previous)
            memory.DeleteDC()
        if source is not None:
            source.DeleteDC()
        if window_dc:
            win32gui.ReleaseDC(hwnd, window_dc)
        if bitmap is not None:
            win32gui.DeleteObject(bitmap.GetHandle())


async def windows_ocr(image: Image.Image, language: str | None) -> dict:
    if not package_identity():
        return {"available": False, "reason": "package_identity_required", "words": []}
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    languages = [item.language_tag for item in OcrEngine.available_recognizer_languages]
    engine = OcrEngine.try_create_from_language(Language(language)) if language else OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return {"available": False, "reason": "ocr_language_unavailable", "languages": languages, "words": []}
    if max(image.size) > OcrEngine.max_image_dimension:
        return {"available": False, "reason": "ocr_image_too_large", "words": []}
    writer = DataWriter()
    bitmap = None
    try:
        writer.write_bytes(image.convert("RGBA").tobytes("raw", "BGRA"))
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                        image.width, image.height, BitmapAlphaMode.IGNORE)
        result = await engine.recognize_async(bitmap)
        words = []
        for line in result.lines:
            for word in line.words:
                rect = word.bounding_rect
                words.append({"text": word.text[:300], "bounds": [rect.x, rect.y, rect.x + rect.width, rect.y + rect.height]})
                if len(words) >= 1000:
                    break
            if len(words) >= 1000:
                break
        return {"available": True, "backend": "windows_ocr", "language": engine.recognizer_language.language_tag,
                "languages": languages, "words": words, "truncated": len(words) == 1000,
                "coordinate_space": "image_pixels", "text_angle": result.text_angle}
    finally:
        if bitmap is not None:
            bitmap.close()
        writer.close()


def ocr(image: Image.Image, backend: str, language: str | None) -> dict:
    state = probe()
    if backend == "auto":
        backend = "windows_ocr" if state["windows_ocr"]["available"] else "tesseract"
    if backend == "windows_ocr":
        if not state["windows_ocr"]["available"]:
            return {**state["windows_ocr"], "backend": backend, "words": []}
        return asyncio.run(windows_ocr(image, language))
    if backend != "tesseract":
        raise ValueError("Unknown OCR backend")
    if not state["tesseract"]["available"]:
        return {"available": False, "backend": backend, "reason": "local_ocr_engine_missing", "words": []}
    import pytesseract

    languages = pytesseract.get_languages()
    if language and language not in languages:
        return {"available": False, "backend": backend, "reason": "ocr_language_unavailable", "languages": languages, "words": []}
    data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT, timeout=8)
    words = []
    for i, text in enumerate(data["text"]):
        if not text.strip() or float(data["conf"][i]) < 0:
            continue
        left, top = int(data["left"][i]), int(data["top"][i])
        words.append({"text": text[:300], "confidence": float(data["conf"][i]),
                      "bounds": [left, top, left + int(data["width"][i]), top + int(data["height"][i])]})
        if len(words) >= 1000:
            break
    return {"available": True, "backend": backend, "language": language, "languages": languages,
            "words": words, "truncated": len(words) == 1000, "coordinate_space": "image_pixels"}


def main() -> None:
    # The helper's coordinate reads are physical pixels. Do not change the server's DPI mode.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        pass
    request = json.loads(sys.stdin.read(65537))
    operation = request["operation"]
    if operation == "probe":
        result = probe()
    elif operation == "capture":
        if request["backend"] not in {"wgc", "printwindow"}:
            raise ValueError("Invalid native capture backend")
        image = wgc(int(request["hwnd"])) if request["backend"] == "wgc" else print_window(int(request["hwnd"]))
        image.save(request["output"], format="PNG")
        result = {"size": list(image.size), "backend": request["backend"]}
    elif operation == "ocr":
        with Image.open(request["image"]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("OCR image exceeds bounds")
            result = ocr(image.convert("RGB"), request["backend"], request.get("language"))
    else:
        raise ValueError("Unsupported native helper operation")
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
