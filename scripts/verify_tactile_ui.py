"""Verify the shipped EXE's material bench and representative manager screens."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image


def verify_icon(target, record):
    image = Image.open(target).convert("RGB")
    x, y, width, height = record["circle_bounds"]
    ratio = record["pixel_ratio"]
    cx, cy = (x + width / 2) * ratio, (y + height / 2 - 1) * ratio
    radius = record["circle_icon_size"] * ratio * .65
    points = [(px, py) for py in range(round(cy - radius), round(cy + radius)) for px in range(round(cx - radius), round(cx + radius)) if min(image.getpixel((px, py))) > 215]
    assert points, "Arrow icon was not rendered"
    left, top = min(px for px, _ in points), min(py for _, py in points)
    right, bottom = max(px for px, _ in points), max(py for _, py in points)
    assert abs((left + right) / 2 - cx) / ratio < 2, "Icon shifted horizontally at high DPI"
    assert abs((top + bottom) / 2 - cy) / ratio < 2, "Icon shifted vertically at high DPI"
    assert (right - left + 1) / (ratio * record["circle_icon_size"]) > .5, "Icon shrank at high DPI"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = {
        "glass-desktop": ["--glass-demo", "--glass-verify"],
        "glass-narrow": ["--glass-demo", "--ui-smoke-size", "420", "900"],
        "glass-dark": ["--glass-demo", "--glass-background", "dark"],
        "glass-hidpi": ["--glass-demo", "--ui-smoke-scale", "1.5"],
    }
    records = []
    for name, arguments in cases.items():
        target = args.out / (name + ".png")
        started = time.time()
        completed = subprocess.run([str(args.executable.resolve()), *arguments, "--glass-capture", str(target.resolve())], timeout=90)
        failure = target.with_suffix(".error.json")
        if completed.returncode or failure.exists() and failure.stat().st_mtime >= started:
            raise RuntimeError(failure.read_text(encoding="utf-8") if failure.exists() else f"EXE exit: {completed.returncode}")
        assert target.stat().st_mtime >= started and target.stat().st_size > 5000
        record = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        assert record["native"] and len(record["states"]) == 6
        verify_icon(target, record)
        if name == "glass-desktop":
            assert len(record["interaction_checks"]) == 16
        elif name == "glass-narrow":
            assert record["width"] == 420
        records.append({"case": name, **record})
        print("Native tactile acceptance passed: " + name, flush=True)
    completed = subprocess.run([sys.executable, str(Path(__file__).with_name("smoke_gui.py")), str(args.executable.resolve()), "--out", str(args.out.resolve()), "--cases", "ready", "selection", "appearance", "advanced", "restore-preview", "upgrade-preview", "dark", "cancel-install", "approval"], timeout=900)
    if completed.returncode:
        raise RuntimeError("Manager acceptance did not pass")
    (args.out / "tactile-summary.json").write_text(json.dumps({"glass_cases_passed": records, "manager_cases_passed": 9}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
