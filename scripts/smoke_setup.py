"""Verify the shipped EXE against temporary client homes and an actual stdio session."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    from installer.app import check_service

    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix="windows-mcp-setup-check-") as temporary:
        test_root = Path(temporary) / "isolated-home"
        subprocess.run([str(executable), "--self-test", str(test_root)], check=True, timeout=360, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        integration = json.loads((test_root / "result.json").read_text(encoding="utf-8"))
        assert integration["install_and_uninstall"] == "PASS" and integration["isolated"]
        stdio = asyncio.run(check_service(executable, home=Path(temporary) / "stdio-home"))
    result = {"version": json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"], "executable": executable.name, "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "size_bytes": executable.stat().st_size, "client_installation": integration, "stdio": stdio}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Some Windows runners expose a cp1252 pipe even though the JSON file is UTF-8.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
