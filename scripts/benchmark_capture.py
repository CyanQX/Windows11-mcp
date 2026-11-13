"""Measure on-demand window capture, without input or exporting the captured pixels."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from windows_mcp.vision import native_bridge
from windows_mcp.vision.capture_worker import worker


def measure(hwnd: int, backend: str, count: int) -> dict:
    if hwnd <= 0 or not 2 <= count <= 50:
        raise ValueError("A live HWND and count between 2 and 50 are required")
    worker.close()
    before = worker.status()
    samples = []
    try:
        for _ in range(count):
            start = time.perf_counter()
            image = native_bridge.capture(hwnd, backend)
            samples.append(round((time.perf_counter() - start) * 1000, 3))
        state = worker.status()
        return {"backend": backend, "count": count, "size": list(image.size), "milliseconds": samples,
                "cold_ms": samples[0], "warm_median_ms": statistics.median(samples[1:]),
                "warm_max_ms": max(samples[1:]), "worker_starts": state["starts"] - before["starts"],
                "worker_requests": state["requests"] - before["requests"],
                "note": "Environment-specific measurements; WGC sessions are recreated per request; no cached frame is returned."}
    finally:
        worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--backend", choices=["wgc", "printwindow"], default="printwindow")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(measure(args.hwnd, args.backend, args.count), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
