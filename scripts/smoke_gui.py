"""Wait for each windowed EXE and require its isolated native-page evidence."""
import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", default=["ready", "status", "appearance", "advanced", "progress-hidden", "edge-preview", "edge-parked", "targets-nested-preview", "targets-nested-parked", "targets-nested-restored", "footer-minimized", "footer-floating-parked", "selection-clear", "update-available", "docking", "approval"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for case in args.cases:
        target = args.out / f"{case}.png"
        target.with_suffix(".error.json").unlink(missing_ok=True)
        completed = subprocess.run([str(args.executable.resolve()), "--ui-smoke", str(target.resolve()), "--ui-smoke-case", case], timeout=180)
        error = target.with_suffix(".error.json")
        if completed.returncode or error.exists():
            raise RuntimeError(error.read_text(encoding="utf-8") if error.exists() else f"Native UI exit {completed.returncode}")
        assert target.is_file() and target.stat().st_size > 1000
        with Image.open(target) as picture:
            assert min(picture.size) >= 90 and picture.convert("RGB").getextrema() != ((0, 0), (0, 0), (0, 0))
        record = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        assert record["case"] == case and record["isolated"]
        if case in {"status", "installed", "selection", "verify", "repair", "progress", "progress-hidden", "backups", "restore-preview", "upgrade-preview", "undo-preview"}:
            assert record["health"]["codex"]["ok"] and record["health"]["codex"]["tool_count"] == 31
        if case in {"undo", "cancel-install"}:
            assert not record["health"]
            assert any(event["phase"] == "undo" and event["level"] == "SUCCESS" for event in record["events"])
        print(f"Native Qt acceptance passed: {case}", flush=True)


if __name__ == "__main__":
    main()
