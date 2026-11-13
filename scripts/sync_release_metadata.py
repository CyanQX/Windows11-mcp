"""Keep manifest version/tool descriptions derived from the registered source tools."""

import json
import asyncio
import tomllib
from pathlib import Path

from windows_mcp.tools import register_all

ROOT = Path(__file__).resolve().parents[1]


class Catalog:
    def __init__(self):
        self.tools = []

    def tool(self, *, name, **options):
        self.tools.append({"name": name, "description": options.get("description", "")})
        return lambda func: func


def main():
    catalog = Catalog()
    register_all(catalog, get_desktop=lambda: None, get_analytics=lambda: None, catalog_only=True)
    path = ROOT / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest["description"] = "Windows automation with verified actions, natural pointer control and persistent operator-approved automatic mode"
    manifest["long_description"] = "Typed Observe/Execute/Verify, TextPattern reads and verified local editing, subtree/known-target observations, event-triggered Watch and measured UIA/Playwright metadata batching. HWND WGC/PrintWindow capture, ROI/downscaling, image-change hints and optional local OCR use a bounded native helper; missing OCR engines/package identity are reported. Fixed full/ui-core/browser profiles, complete SDK-specific static responses, doctor and read-only Microsoft companion discovery are available. No ODR registration, MSIX identity or native File Explorer/Settings connection is implied. Cooperative waits, tracked pending UIA actions and atomic scoped native text overwrites remain available. Every tool traverses PolicyGate. Persistent automatic mode is explicitly enabled by the local operator and covers connected clients, applications, HTTP(S) origins and already-enabled capabilities until manually disabled, across reconnects and restarts. Capability switches, workspace/protected paths and cancellation still apply. Legacy trust retains its client/application/tool/action scope; HTTP origins need separate authorization outside persistent mode. Empty review windows do not pause input, while authorization controls remain protected. Installation, authorization migration and qualification limits are documented in README."
    manifest["long_description"] += " Version 1.6.0 adds opt-in pointer-first automatic clicks, shared SendInput gestures, final target checks, adaptive feedback-controlled motion and pinned-control tracking, bounded dwell, external-input handoff, cancellation/partial-input cleanup and dispatch/motion evidence. Raw coordinates have no control tracking; input acceptance is not task completion. Cooperating processes share a logon-session desktop mutex. Window capture reuses a bounded native worker; visual clicks recheck a local pixel patch before pressing. Changed content requires a new observation; there is no automatic relocation or replay. WGC capture sessions remain per-request and other automation software does not participate."
    manifest["tools"] = catalog.tools
    from windows_mcp.infrastructure.catalog import export_manifest

    manifest = asyncio.run(export_manifest(manifest, "full"))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
