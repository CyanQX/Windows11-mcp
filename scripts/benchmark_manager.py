"""Measure only our manager windows, in disposable homes, without mouse input.

First process launch is measured; this does not flush the OS cache, reboot the
machine, or claim a physical cold-boot measurement. No production config changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

import psutil
import win32con
import win32gui
import win32process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def family(process):
    try:
        root = psutil.Process(process.pid)
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def windows(process):
    pids = {item.pid for item in family(process)}
    found = []
    def each(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                found.append((hwnd, pid, win32gui.GetWindowText(hwnd)))
        return True
    win32gui.EnumWindows(each, None)
    return found


def measure(executable, home, *, attempts=3, repeated=8):
    from scripts.smoke_actilo import environment
    env, samples, notes = environment(home), [], []
    home.mkdir()
    for index in range(attempts):
        start = time.perf_counter()
        process = subprocess.Popen([str(executable)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            main = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and main is None:
                main = next((item for item in windows(process) if item[2].startswith(("Actilo", "Windows MCP", "Windows-MCP"))), None)
                if process.poll() is not None:
                    raise RuntimeError("Manager exited before showing a window: " + str(process.returncode))
                time.sleep(.01)
            if main is None:
                raise TimeoutError("Manager failed to display within 45 seconds")
            first_visible = (time.perf_counter() - start) * 1000
            gui = psutil.Process(main[1])
            time.sleep(4)
            before = gui.cpu_times()
            idle_start = time.monotonic()
            time.sleep(2)
            after = gui.cpu_times()
            cpu = 100 * ((after.user + after.system) - (before.user + before.system)) / (time.monotonic() - idle_start)
            row = {"attempt": index + 1, "first_visible_ms": round(first_visible, 2),
                   "idle_rss_mb": round(gui.memory_info().rss / 1048576, 2), "idle_cpu_one_core_percent": round(cpu, 3)}
            mark = home / ".windows-mcp/release-notes-shown.json"
            note = mark.read_bytes() if mark.exists() else None
            notes.append(note)
            if index == 0:
                children = [subprocess.Popen([str(executable)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(repeated)]
                try:
                    assert all(child.wait(timeout=30) == 0 for child in children)
                    assert len([item for item in windows(process) if item[2].startswith(("Actilo", "Windows MCP", "Windows-MCP"))]) == 1
                    row.update(rapid_launches=repeated, manager_windows=1)
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.kill()
                            child.wait(5)
            samples.append(row)
        finally:
            for hwnd, _, _ in windows(process):
                win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                for member in reversed(family(process)):
                    try:
                        member.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.wait(5)
                raise RuntimeError("Manager did not exit cleanly")
    if notes[0] is not None:
        assert all(item == notes[0] for item in notes), "Release notes acknowledgement changed across launches"
    with executable.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"exe_sha256": fingerprint,
            "samples": samples, "successful_starts": len(samples), "attempted_starts": attempts,
            "median_visible_ms": statistics.median(row["first_visible_ms"] for row in samples),
            "release_notes_stable_across_restarts": notes[0] is not None,
            "method": "visible HWND; 2-second idle sample; warm OS cache not controlled"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="actilo-startup-benchmark-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        result = {"candidate": measure(args.candidate.resolve(), root / "candidate")}
        if args.baseline:
            result["baseline"] = measure(args.baseline.resolve(), root / "baseline")
            result["visible_ms_change"] = round(result["candidate"]["median_visible_ms"] - result["baseline"]["median_visible_ms"], 2)
        result["not_measured"] = ["Windows reboot", "physical cold boot", "second Windows account", "artificial low-memory conditions"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
