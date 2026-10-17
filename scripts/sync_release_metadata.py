"""Keep manifest version/tool descriptions derived from the registered source tools."""

import json
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
    register_all(catalog, get_desktop=lambda: None, get_analytics=lambda: None)
    path = ROOT / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest["description"] = "Semantic Windows automation with independent verification, scoped permissions and managed Playwright"
    manifest["long_description"] = "Semantic-first Observe/Execute/Verify, bounded multi-subscriber Watch, isolated Playwright browser contexts, native scoped text files and window-bound visual fallback. Legacy tools remain registered and pass through the same PolicyGate. R2 requires an exact, session-bound, expiring, one-use local operator approval by default; the local operator may explicitly enable a signed, revocable full-authorization grant for R2 only, while R3 stays disabled. Tested on Windows 11; application coverage, mixed-DPI multi-monitor and Windows 10 qualification limits are documented in README."
    manifest["tools"] = catalog.tools
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
