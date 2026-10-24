"""Wait for each windowed EXE and require its isolated native-page evidence."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from installer.ui.scenarios import SMOKE_CASES
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=SMOKE_CASES, default=["ready", "status", "appearance", "advanced", "progress-hidden", "selection-clear", "update-available", "approval", "settings-update", "paths", "selection-bottom", "status-details", "approval-default-automatic", "choice-popup", "choice-strength", "fixed-layout", "about", "privacy", "approval-locked"])
    args = parser.parse_args()
    from installer.storage import check_path
    executable = check_path(args.executable)
    if executable.name != "Windows-MCP.exe" or not executable.is_file():
        parser.error("Native acceptance requires the built Windows-MCP.exe")
    args.out = check_path(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    for requested in args.cases:
        case = next(allowed for allowed in SMOKE_CASES if allowed == requested)
        target = args.out / f"{case}.png"
        target.with_suffix(".error.json").unlink(missing_ok=True)
        completed = subprocess.run([str(executable), f"--ui-smoke={target.resolve()}", f"--ui-smoke-case={case}"], timeout=180)
        error = target.with_suffix(".error.json")
        if completed.returncode or error.exists():
            raise RuntimeError(error.read_text(encoding="utf-8") if error.exists() else f"Native UI exit {completed.returncode}")
        assert target.is_file() and target.stat().st_size > 1000
        with Image.open(target) as picture:
            assert min(picture.size) >= 90 and picture.convert("RGB").getextrema() != ((0, 0), (0, 0), (0, 0))
        record = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        assert record["case"] == case and record["isolated"]
        if case in {"status", "status-details", "installed", "selection", "selection-bottom", "verify", "repair", "progress", "progress-hidden", "backups", "restore-preview", "upgrade-preview", "undo-preview"}:
            assert record["health"]["codex"]["ok"] and record["health"]["codex"]["tool_count"] == 31
        if case in {"undo", "cancel-install"}:
            assert not record["health"]
            assert any(event["phase"] == "undo" and event["level"] == "SUCCESS" for event in record["events"])
        print(f"Native Qt acceptance passed: {case}", flush=True)


if __name__ == "__main__":
    main()
