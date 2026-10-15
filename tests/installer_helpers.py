"""Isolated manager fixtures; never discover or modify live clients."""

import json
from pathlib import Path

from installer.backend import Installer
from installer.health import HealthResult
from installer.models import Presence
from installer.operations import InstallerService


class FoundClients:
    def detect(self, profile):
        return Presence.DETECTED, "isolated-desktop-fixture"


class Healthy:
    async def probe(self, executable):
        return HealthResult(True, "VERIFIED", "隔离测试连接通过", "2026-10-15T00:00:00Z", 1, 31, "1.2.1", "READY")


def make_service(tmp_path: Path, *, discovery=None, health=None):
    skill = tmp_path / "payload"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: windows-mcp\ndescription: Test\n---\nUse connected MCP.\n", encoding="utf-8")
    (skill / "reference.md").write_text("fixture reference", encoding="utf-8")
    executable = tmp_path / "setup.exe"
    executable.write_bytes(b"MZ isolated test binary")
    manifest = json.loads((Path(__file__).resolve().parents[1] / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "1.2.1"
    return InstallerService(Installer(tmp_path / "home", tmp_path / "app", skill, executable, {}), manifest, discovery=discovery or FoundClients(), health=health or Healthy())
