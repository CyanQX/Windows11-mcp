"""Bounded process boundary for native components; timeout kills the entire worker."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image


def request(payload: dict, *, timeout: float = 12) -> dict:
    command = [sys.executable, "native-helper"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "windows_mcp.vision.native_worker"]
    import win32api
    import win32job
    import pywintypes

    job, process = None, None
    try:
        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        win32job.AssignProcessToJobObject(job, int(process._handle))
        stdout, _ = process.communicate(json.dumps(payload), timeout=timeout)
    except pywintypes.error as exc:
        raise RuntimeError("Native component process boundary is unavailable") from exc
    finally:
        # Closes native threads and any OCR engine children as one unit, including timeouts.
        try:
            if job is not None:
                win32api.CloseHandle(job)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
    if process.returncode:
        raise RuntimeError("Native component failed; run doctor for dependency and session diagnostics")
    if len(stdout) > 1000000:
        raise RuntimeError("Native component response exceeded its limit")
    value = json.loads(stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Invalid native component response")
    return value


@lru_cache(maxsize=1)
def capabilities() -> dict:
    try:
        return request({"operation": "probe"}, timeout=8)
    except (OSError, ValueError, RuntimeError, TypeError, subprocess.TimeoutExpired):
        return {"available": False, "reason": "native_probe_failed"}


def capture(hwnd: int, backend: str) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="wmcp-capture-") as directory:
        path = Path(directory) / "frame.png"
        result = request({"operation": "capture", "hwnd": hwnd, "backend": backend, "output": str(path)})
        if not path.is_file() or path.stat().st_size > 64000000:
            raise RuntimeError("Native capture did not produce a bounded image")
        with Image.open(path) as image:
            if image.width * image.height > 16000000 or list(image.size) != result.get("size"):
                raise RuntimeError("Native frame size is invalid")
            return image.convert("RGB").copy()


def recognize(data: bytes, *, backend: str = "auto", language: str | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="wmcp-ocr-") as directory:
        path = Path(directory) / "image.png"
        with Image.open(io.BytesIO(data)) as image:
            image.save(path, format="PNG")
        return request({"operation": "ocr", "image": str(path), "backend": backend, "language": language}, timeout=15)
