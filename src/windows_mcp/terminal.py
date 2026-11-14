"""Bounded, cancellable user-token terminal sessions with OS-owned process trees."""
from __future__ import annotations

import base64
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from windows_mcp.infrastructure import execution_security
from windows_mcp.infrastructure.execution_security import ExecutionDenied

LIMIT = 256 * 1024


def command_line(shell, command=None):
    shell = shell.casefold().removesuffix(".exe")
    if shell not in {"powershell", "pwsh", "cmd"}:
        raise ValueError("shell must be powershell, pwsh or cmd; use App for the Windows Terminal host wt.exe")
    if shell in {"cmd", "powershell"}:
        import win32api
        root = Path(win32api.GetSystemDirectory())
        executable = root / ("cmd.exe" if shell == "cmd" else "WindowsPowerShell/v1.0/powershell.exe")
        executable = str(executable) if executable.is_file() else None
    else:
        from windows_mcp.powershell.service import _prepare_env
        executable = shutil.which(shell + ".exe", path=_prepare_env().get("PATH"))
    if executable is None:
        raise FileNotFoundError(shell + ".exe is not installed")
    if shell == "cmd":
        return [executable, "/d", "/u", "/q", "/c" if command is not None else "/k", *([command] if command is not None else [])]
    args = [executable, "-NoLogo", "-NoProfile", "-NonInteractive"]
    if shell == "powershell":
        args += ["-OutputFormat", "Text"]
    if command is None:
        return [*args, "-NoExit", "-Command", "-"]
    script = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $OutputEncoding=[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); " + command
    return [*args, "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode("ascii")]


def reject_elevation(text):
    """Additional intent guard. Actual authority comes from tokens, never this list."""
    import re
    if re.search(r"(?i)(\brunas(?:\.exe)?\b|(?:-verb|verb\s*=)\s*['\"]?runas|elevation:administrator|trustedinstaller|psexec.*(?:-s\b)|(?:/rl\s+|runlevel\s+)highest|\b(?:sc(?:\.exe)?\s+create|new-service|set-tokenprivilege)\b)", text):
        raise ExecutionDenied("ELEVATION_REQUEST_DENIED: AI cannot request UAC, services, tasks or alternate privileged tokens")


class TerminalProcess:
    def __init__(self, args, cwd, *, command=None):
        from windows_mcp.infrastructure.authorization import check_dispatch
        check_dispatch()
        execution_security.require_standard()
        reject_elevation(subprocess.list2cmdline(args) + " " + (command or ""))
        import win32api
        import win32con
        import win32job
        import win32process
        import psutil

        self.started = time.monotonic()
        self.cwd, self.command = str(Path(cwd).resolve()), command
        self.encoding = "utf-16le" if Path(args[0]).name.casefold() == "cmd.exe" else "utf-8"
        self.input_encoding = "utf-8"
        if Path(args[0]).name.casefold() == "cmd.exe":
            import ctypes
            self.input_encoding = "cp" + str(ctypes.windll.kernel32.GetOEMCP())
        self.cancelled = self.timed_out = False
        self.buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self.truncated = False
        self.lock = threading.Lock()
        self.lifecycle = threading.RLock()
        self.job = win32job.CreateJobObject(None, "")
        self.process = None
        self.readers = []
        try:
            limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
            # Never allow breakaway. Desktop/system UI changes are unavailable
            # to terminal jobs, independently of the human approval mode.
            win32job.SetInformationJobObject(self.job, win32job.JobObjectBasicUIRestrictions, {"UIRestrictionsClass": 0xFF})
            from windows_mcp.infrastructure.process_env import independent_environment
            from windows_mcp.powershell.service import _prepare_env
            environment = independent_environment(_prepare_env())
            environment["NO_COLOR"] = "1"
            self.process = subprocess.Popen(args, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=environment, shell=False, close_fds=True,
                creationflags=win32con.CREATE_SUSPENDED | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
            win32job.AssignProcessToJobObject(self.job, int(self.process._handle))
            execution_security.require_standard(self.process.pid)  # Before the first untrusted instruction.
            threads = psutil.Process(self.process.pid).threads()
            if len(threads) != 1:
                raise ExecutionDenied("Cannot identify suspended terminal entry thread")
            thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, threads[0].id)
            try:
                win32process.ResumeThread(thread)
            finally:
                thread.Close()
            for name in self.buffers:
                reader = threading.Thread(target=self._read, args=(name,), name="actilo-terminal-" + name, daemon=True)
                self.readers.append(reader)
                reader.start()
        except BaseException:
            if self.process is not None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.job.Close()
            raise

    def _read(self, name):
        stream = getattr(self.process, name)
        try:
            while chunk := stream.read1(8192):
                with self.lock:
                    room = LIMIT - len(self.buffers[name])
                    self.buffers[name].extend(chunk[:max(0, room)])
                    self.truncated |= len(chunk) > room
        finally:
            stream.close()

    def write(self, text):
        from windows_mcp.infrastructure.authorization import check_dispatch
        check_dispatch()
        execution_security.require_standard()
        reject_elevation(text)
        if len(text.encode("utf-8")) > 65536 or self.process.poll() is not None:
            raise ValueError("Terminal input exceeds 64 KiB or process has exited")
        self.process.stdin.write(text.encode(self.input_encoding, errors="strict"))
        self.process.stdin.flush()

    def status(self, *, encoding="auto", include_raw=False):
        import codecs
        import ctypes
        if encoding == "oem":
            encoding = "cp" + str(ctypes.windll.kernel32.GetOEMCP())
        elif encoding == "ansi":
            encoding = "cp" + str(ctypes.windll.kernel32.GetACP())
        if encoding != "auto":
            codecs.lookup(encoding)
        def decode(value):
            selected = encoding
            if selected == "auto":
                if value.startswith((b"\xff\xfe", b"\xfe\xff")):
                    selected = "utf-16"
                elif value.startswith(b"\xef\xbb\xbf"):
                    selected = "utf-8-sig"
                elif b"\0" in value[:4096] and self.encoding == "utf-16le":
                    selected = "utf-16le"
                else:
                    try:
                        value.decode("utf-8")
                        selected = "utf-8"
                    except UnicodeDecodeError:
                        selected = self.encoding if self.encoding == "utf-16le" else self.input_encoding
            return value.decode(selected, errors="replace"), selected
        with self.lock:
            output = {key: bytes(value) for key, value in self.buffers.items()}
            decoded = {key: decode(value) for key, value in output.items()}
            row = {"pid": self.process.pid, "cwd": self.cwd, "command": self.command,
                    "exit_code": self.process.poll(), "running": self.process.poll() is None,
                    "stdout": decoded["stdout"][0], "stderr": decoded["stderr"][0],
                    "output_encoding": {key: value[1] for key, value in decoded.items()},
                    "encoding_auto_detected": encoding == "auto",
                    "input_encoding": self.input_encoding,
                    "output_truncated": self.truncated, "cancelled": self.cancelled, "timed_out": self.timed_out,
                    "duration_seconds": round(time.monotonic() - self.started, 3), "privilege": "non-elevated"}
            if include_raw:
                row["raw_base64"] = {key: base64.b64encode(value).decode("ascii") for key, value in output.items()}
            return row

    def stop(self, *, timed_out=False):
        import win32job
        with self.lifecycle:
            self.cancelled, self.timed_out = not timed_out, timed_out
            if self.job is not None:
                win32job.TerminateJobObject(self.job, 1)

    def close(self):
        with self.lifecycle:
            if self.job is not None:
                self.job.Close()  # Also terminates orphan descendants after the shell exits.
                self.job = None
            self.process.wait(timeout=5)
            for reader in self.readers:
                reader.join(2)
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()


class Terminals:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()

    def start(self, owner, shell, cwd):
        with self.lock:
            if len(self.sessions) >= 16:
                raise RuntimeError("Terminal capacity reached; close an existing session")
            item = TerminalProcess(command_line(shell), cwd)
            key = "terminal_" + uuid.uuid4().hex
            self.sessions[key] = (owner, item)
            return {"terminal_id": key, **item.status()}

    def get(self, owner, key):
        with self.lock:
            record = self.sessions.get(key)
            if record is None or record[0] != owner:
                raise PermissionError("Terminal belongs to another session or has closed")
            return record[1]

    def close(self, owner, key):
        item = self.get(owner, key)
        item.stop()
        item.close()
        with self.lock:
            self.sessions.pop(key, None)
        return item.status()

    def close_owner(self, owner):
        for key, (known, _) in list(self.sessions.items()):
            if owner is None or owner == known:
                self.close(known, key)


def execute(command, *, shell="powershell", cwd=None, timeout=30, cancelled=None, encoding="auto", include_raw=False):
    if not 0 < timeout <= 3600 or len(command.encode("utf-8")) > 65536:
        raise ValueError("Command exceeds 64 KiB or timeout outside (0, 3600]")
    item = TerminalProcess(command_line(shell, command), cwd or Path.home(), command=command)
    try:
        while item.process.poll() is None:
            if cancelled is not None and cancelled.is_set():
                item.stop()
                break
            if time.monotonic() - item.started >= timeout:
                item.stop(timed_out=True)
                break
            time.sleep(0.05)
    finally:
        item.close()
    return item.status(encoding=encoding, include_raw=include_raw)
