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
    parser.add_argument("--require-native-components", action="store_true")
    args = parser.parse_args()
    executable = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix="windows-mcp-setup-check-") as temporary:
        test_root = Path(temporary) / "isolated-home"
        subprocess.run([str(executable), "--self-test", str(test_root)], check=True, timeout=360, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        integration = json.loads((test_root / "result.json").read_text(encoding="utf-8"))
        assert integration["install_and_uninstall"] == "PASS" and integration["isolated"]
        stdio = asyncio.run(check_service(executable, home=Path(temporary) / "stdio-home"))
        if args.require_native_components:
            # Exercise the exact hidden streaming command in the shipped EXE, rather
            # than assuming that source-only protocol tests cover the bootloader.
            messages = "".join(json.dumps({"id": i, "request": {"operation": "probe"}}) + "\n" for i in (1, 2))
            streamed = subprocess.run([str(executable), "native-helper", "--stream"], input=messages,
                check=True, timeout=45, capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW)
            responses = [json.loads(line) for line in streamed.stdout.splitlines()]
            assert [item["id"] for item in responses] == [1, 2]
            assert all(not item.get("error") and item["result"]["wgc"]["available"] for item in responses)
            assert responses[0]["result"]["windows_ocr"]["dependencies_installed"], "Frozen WinRT OCR bindings were not detected"
            stdio["native_stream_two_requests"] = True
            stdio["native_maintenance_probe"] = responses[0]["result"]
    result = {"version": json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"], "executable": executable.name, "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "size_bytes": executable.stat().st_size, "client_installation": integration, "stdio": stdio}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Some Windows runners expose a cp1252 pipe even though the JSON file is UTF-8.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
