"""Check a release's versions and every packaged runtime source before publishing."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import re
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(dist: Path, tag: str | None = None) -> dict:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(item for item in lock["package"] if item["name"] == project["name"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert manifest["version"] == locked["version"] == version, "Release versions disagree"
    assert f"<h1>Windows-MCP {version}</h1>" in readme, "README version disagrees"
    if tag is not None:
        assert tag == f"v{version}", "Git tag disagrees with package version"
    tools = [tool["name"] for tool in manifest["tools"]]
    documented = set(re.findall(r"^\| `([A-Za-z]+)` \|", readme, flags=re.M))
    assert len(tools) == len(set(tools)) and set(tools) == documented, "Tool catalog disagrees"

    wheel_paths = sorted(dist.glob("*.whl"))
    sdist_paths = sorted(dist.glob("*.tar.gz"))
    assert len(wheel_paths) == len(sdist_paths) == 1, "Expected exactly one wheel and sdist"
    wheel, sdist = wheel_paths[0], sdist_paths[0]
    source_files = {
        path.relative_to(ROOT / "src").as_posix(): path.read_bytes()
        for path in (ROOT / "src" / "windows_mcp").rglob("*.py")
    }
    assert source_files, "Runtime source is missing"
    with zipfile.ZipFile(wheel) as archive:
        assert archive.testzip() is None, "Wheel CRC failed"
        names = archive.namelist()
        packaged = {name for name in names if name.startswith("windows_mcp/") and name.endswith(".py")}
        assert packaged == set(source_files), "Wheel has missing or obsolete runtime modules"
        for name, content in source_files.items():
            assert archive.read(name) == content, f"Wheel source differs: {name}"
        metadata = BytesParser().parsebytes(archive.read(next(n for n in names if n.endswith(".dist-info/METADATA"))))
        assert metadata["Name"] == project["name"] and metadata["Version"] == version
        entries = configparser.ConfigParser()
        entries.read_string(archive.read(next(n for n in names if n.endswith(".dist-info/entry_points.txt"))).decode())
        assert dict(entries["console_scripts"]) == project["scripts"], "Installed command differs"

    with tarfile.open(sdist, "r:gz") as archive:
        members = {member.name.split("/", 1)[1]: member for member in archive.getmembers() if member.isfile() and "/" in member.name}
        for name, content in source_files.items():
            path = "src/" + name
            assert path in members, f"Source distribution is missing: {path}"
            assert archive.extractfile(members[path]).read() == content, f"Source distribution differs: {path}"
        required = ["README.md", "CHANGELOG.md", "LICENSE.md", "CLAUDE.md", "pyproject.toml", "manifest.json", "uv.lock", "MANIFEST.in"]
        required.extend(path.relative_to(ROOT).as_posix() for folder in ("assets", "tests", "scripts", ".github", ".claude", "installer", "skills") for path in (ROOT / folder).rglob("*") if path.is_file() and path.suffix in {".py", ".png", ".svg", ".txt", ".json", ".yml", ".md", ".iss"} and "__pycache__" not in path.parts)
        for name in required:
            assert name in members, f"Source distribution is missing release file: {name}"
            assert archive.extractfile(members[name]).read() == (ROOT / name).read_bytes(), f"Source distribution differs: {name}"
        metadata = BytesParser().parsebytes(archive.extractfile(members["PKG-INFO"]).read())
        assert metadata["Version"] == version, "Source distribution version differs"

    if list(dist.glob("*.exe")) or list(dist.glob("*.zip")):
        import sys
        sys.path[:0] = [str(ROOT), str(ROOT / "src")]
        from installer.distribution import portable_name, setup_name, verify_portable
        setup = dist / setup_name(version)
        assert setup.is_file(), "Real installation wizard is missing"
        portable = verify_portable(dist / portable_name(version), version)
        frozen = json.loads((dist / "setup-source-verification.json").read_text(encoding="utf-8"))
        build = json.loads((dist / "installer-build.json").read_text(encoding="utf-8"))
        assert portable["payload_sha256"] == frozen["sha256"] == build["payload_sha256"], "Installer and portable payload differ"
        assert hashlib.sha256(setup.read_bytes()).hexdigest() == build["sha256"], "Installer wrapper differs"
        for name in ("installer-validation.json", "portable-validation.json"):
            report = json.loads((dist / name).read_text(encoding="utf-8"))
            assert report["sha256"] == hashlib.sha256((setup if name.startswith("installer") else dist / portable_name(version)).read_bytes()).hexdigest()
            assert report["actual_executable_validation"]["stdio"]["ok"], name
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in dist.iterdir() if path.is_file() and not path.name.startswith(".") and path.name not in {"SHA256SUMS.txt", "distribution-verification.json"}}
    (dist / "SHA256SUMS.txt").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())), encoding="utf-8")
    return {"version": version, "tag": tag, "tool_count": len(tools), "runtime_source_files": len(source_files), "package_source_matches": True, "artifacts": hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(json.dumps(verify(args.dist, args.tag), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
