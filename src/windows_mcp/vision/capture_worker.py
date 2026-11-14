"""Serialized, reusable native process. A timeout discards its entire Windows job."""

from __future__ import annotations

import atexit
import json
import queue
import subprocess
import sys
import threading
import time

import pywintypes
import win32api
import win32job

MAX_RESPONSE = 1_000_000


class CaptureWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = self.job = self.reader = None
        self.responses = queue.Queue(maxsize=1)
        self.starts = self.requests = self.timeouts = 0
        self.sequence = 0
        self.last_elapsed_ms = None

    def _start(self):
        from windows_mcp.infrastructure.process_env import independent_environment
        command = ([sys.executable, "native-helper", "--stream"] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "windows_mcp.vision.native_worker", "--stream"])
        try:
            self.job = win32job.CreateJobObject(None, "")
            limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW, env=independent_environment())
            win32job.AssignProcessToJobObject(self.job, int(self.process._handle))
            self.responses = queue.Queue(maxsize=1)
            self.reader = threading.Thread(target=self._read, args=(self.process.stdout, self.responses),
                                           daemon=True, name="wmcp-capture-response")
            self.reader.start()
            self.starts += 1
        except (OSError, pywintypes.error) as exc:
            self._stop()
            raise RuntimeError("Native capture process boundary is unavailable") from exc

    @staticmethod
    def _read(stdout, responses):
        try:
            while True:
                line = stdout.readline(MAX_RESPONSE + 1)
                if not line or len(line) > MAX_RESPONSE or not line.endswith("\n"):
                    responses.put_nowait(RuntimeError("Native capture worker closed or exceeded response bounds"))
                    return
                responses.put_nowait(json.loads(line))
        except (OSError, ValueError, queue.Full) as exc:
            try:
                responses.put_nowait(RuntimeError(f"Invalid native capture response: {type(exc).__name__}"))
            except queue.Full:
                pass

    def _stop(self):
        process, job, reader = self.process, self.job, self.reader
        self.process = self.job = self.reader = None
        try:
            if job is not None:
                win32api.CloseHandle(job)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if reader is not None:
                    reader.join(timeout=2)
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()

    def request(self, payload: dict, *, timeout: float = 12) -> dict:
        if not 0 < timeout <= 60:
            raise ValueError("Native request timeout must be 0..60 seconds")
        started = time.monotonic()
        if not self.lock.acquire(timeout=timeout):
            raise TimeoutError("Native capture worker busy; request was not sent")
        try:
            if time.monotonic() - started >= timeout:
                raise TimeoutError("Native capture request expired before dispatch")
            if self.process is None or self.process.poll() is not None:
                self._stop()
                self._start()
            self.sequence += 1
            envelope = {"id": self.sequence, "request": payload}
            encoded = json.dumps(envelope)
            if len(encoded.encode("utf-8")) > 65536:
                raise ValueError("Native capture request exceeded its limit")
            self.requests += 1
            try:
                self.process.stdin.write(encoded + "\n")
                self.process.stdin.flush()
                remaining = max(0, timeout - (time.monotonic() - started))
                result = self.responses.get(timeout=remaining)
                if isinstance(result, Exception):
                    raise result
                if not isinstance(result, dict) or result.get("id") != self.sequence or not isinstance(result.get("result"), dict):
                    raise RuntimeError("Native capture response did not match its request")
            except queue.Empty as exc:
                self.timeouts += 1
                self._stop()
                raise subprocess.TimeoutExpired("persistent native capture", timeout) from exc
            except (OSError, ValueError, RuntimeError):
                self._stop()
                raise
            # A provider error is a valid response; it does not desynchronize the stream.
            if result.get("error"):
                raise RuntimeError("Native capture provider failed: " + result["error"])
            return result["result"]
        finally:
            self.last_elapsed_ms = round((time.monotonic() - started) * 1000, 2)
            self.lock.release()

    def status(self):
        process = self.process
        return {"running": process is not None and process.poll() is None,
                "pid": process.pid if process is not None and process.poll() is None else None,
                "starts": self.starts, "requests": self.requests, "timeouts": self.timeouts,
                "last_elapsed_ms": self.last_elapsed_ms}

    def close(self):
        with self.lock:
            self._stop()


worker = CaptureWorker()
atexit.register(worker.close)
