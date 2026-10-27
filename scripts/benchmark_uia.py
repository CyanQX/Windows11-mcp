"""Read-only UIA traversal benchmark. No titles, names, text or raw pointers are persisted.

Example: python scripts/benchmark_uia.py --window 12345 --samples 3 --output reports/uia.json
Use --inventory to list live process names and HWNDs, then choose windows explicitly.
The cached strategy is opt-in: a fixture result cannot justify changing all providers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from windows_mcp.desktop import elements
from windows_mcp.runtime import call_on_desktop


def benchmark(window, *, samples: int, limit: int, max_depth: int) -> dict:
    observations = []
    for iteration in range(samples):
        # Alternate order to reduce warm-cache order bias.
        for strategy in (["bounded", "cached"] if iteration % 2 == 0 else ["cached", "bounded"]):
            metrics = {}
            nodes, _, diagnostics = elements.walk_bounded(window, limit=limit, max_depth=max_depth,
                                                           strategy=strategy, metrics=metrics)
            # Digest identities/rectangles only. A changed digest can reflect live UI changes;
            # it is not automatically a traversal regression or an equivalence proof.
            identity = [(node.runtime_id, node.control_type, node.rect) for node in nodes]
            metrics.update(sample=iteration, identity_digest=hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                           diagnostics=diagnostics)
            observations.append(metrics)
    summaries = {}
    for strategy in ("bounded", "cached"):
        rows = [row for row in observations if row["strategy"] == strategy]
        summaries[strategy] = {"median_ms": statistics.median(row["elapsed_ms"] for row in rows),
            "median_provider_calls": statistics.median(row["provider_calls"] for row in rows),
            "min_returned": min(row["returned"] for row in rows),
            "max_returned": max(row["returned"] for row in rows),
            "truncated_samples": sum(row["truncated"] for row in rows)}
    return {"window_id": window.handle, "process": elements.process_name(window.pid),
            "summary": summaries, "identity_stable": len({row["identity_digest"] for row in observations}) == 1,
            "samples": observations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--window", action="append", type=int, default=[])
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.samples <= 10 or not 1 <= args.limit <= 300 or not 1 <= args.max_depth <= 20:
        parser.error("samples must be 1..10, limit 1..300, max-depth 1..20")
    if args.inventory:
        rows = call_on_desktop(lambda: [{"window_id": window.handle,
            "process": elements.process_name(window.pid)} for window in elements.top_windows()])
        print(json.dumps(rows, indent=2))
        return
    if not args.window or args.output is None:
        parser.error("choose explicit --window handles and an --output file")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "python": sys.version,
        "os": platform.platform(), "limit": args.limit, "max_depth": args.max_depth,
        "notes": ["Provider call count covers traversal FindAllBuildCache calls, not every COM property access.",
                  "Cached mode may fetch a larger provider batch; no active provider call can be force-cancelled.",
                  "Live changes can alter digests; compare counts, truncation and identity before interpreting time."],
        "windows": []}
    for hwnd in args.window:
        def run():
            window = elements.window_info(hwnd)
            if window is None:
                raise ValueError("Selected window no longer exists")
            return benchmark(window, samples=args.samples, limit=args.limit, max_depth=args.max_depth)
        report["windows"].append(call_on_desktop(run))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "windows": [
        {key: value for key, value in row.items() if key != "samples"} for row in report["windows"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
