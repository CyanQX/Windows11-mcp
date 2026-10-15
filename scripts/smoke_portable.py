"""Verify the actual ZIP after extraction, including adjacent data and real MCP."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    from installer.distribution import portable_root, verify_portable

    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    result = verify_portable(args.archive, version)
    with tempfile.TemporaryDirectory(prefix="windows-mcp-portable-check-") as temporary:
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(temporary)  # Exact fixed entries already checked above.
        bundle = Path(temporary) / portable_root(version)
        executable = bundle / "Windows-MCP.exe"
        assert hashlib.sha256(executable.read_bytes()).hexdigest() == result["payload_sha256"]
        contract = Path(temporary) / "portable-contract.json"
        completed = subprocess.run([str(executable), "--portable-check", str(contract)], timeout=90)
        if completed.returncode:
            error = contract.with_suffix(".error.json")
            raise RuntimeError(error.read_text(encoding="utf-8") if error.is_file() else f"Portable acceptance exit {completed.returncode}")
        result["portable_contract"] = json.loads(contract.read_text())
        smoke = Path(temporary) / "stdio-validation.json"
        subprocess.run([sys.executable, str(ROOT / "scripts/smoke_setup.py"), str(executable), "--output", str(smoke)], check=True, timeout=420, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result["actual_executable_validation"] = json.loads(smoke.read_text(encoding="utf-8"))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
