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
    manifest["description"] = "Semantic Windows automation with independent verification, scoped permissions and managed Playwright"
    manifest["long_description"] = "Typed Observe/Execute/Verify, TextPattern reads and verified local editing, subtree/known-target observations, event-triggered Watch and measured UIA/Playwright metadata batching. HWND WGC/PrintWindow capture, ROI/downscaling, image-change hints and optional local OCR use a bounded native helper; missing OCR engines/package identity are reported. Fixed full/ui-core/browser profiles, complete SDK-specific static responses, doctor and read-only Microsoft companion discovery are available. No ODR registration, MSIX identity or native File Explorer/Settings connection is implied. Cooperative waits, tracked pending UIA actions and atomic scoped native text overwrites remain available. Every tool traverses PolicyGate. R2 trust is client/application/tool/action scoped; sensitive input remains R3. HTTP origins require independent authorization. Installation, authorization migration and qualification limits are documented in README."
    manifest["tools"] = catalog.tools
    from windows_mcp.infrastructure.catalog import export_manifest

    manifest = asyncio.run(export_manifest(manifest, "full"))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
