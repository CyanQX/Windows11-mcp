"""Rebuild the small, attributed Fluent icon subset used by the native manager.

Build-only dependency: resvg-py. Runtime uses bundled PNGs, no network or SVG engine.
"""

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import requests
import resvg_py

ROOT = Path(__file__).resolve().parents[1] / "installer" / "resources"
ICONS = {"settings": "Settings", "search": "Search", "link": "Link", "repair": "Wrench", "backup": "Database", "restore": "History", "folder": "Folder", "update": "Arrow Download", "shield": "Shield Checkmark", "info": "Info", "delete": "Delete", "chevron": "Chevron Down", "check": "Checkmark Circle", "refresh": "Arrow Sync", "copy": "Copy", "document": "Document", "dismiss": "Dismiss Circle"}


def main():
    session = requests.Session()
    response = session.get("https://api.github.com/repos/microsoft/fluentui-system-icons/commits/main", timeout=30)
    response.raise_for_status()
    commit = response.json()["sha"]
    source = f"https://raw.githubusercontent.com/microsoft/fluentui-system-icons/{commit}"
    dest = ROOT / "icons"
    dest.mkdir(parents=True, exist_ok=True)
    records = []
    for name, folder in ICONS.items():
        filename = f"ic_fluent_{folder.lower().replace(' ', '_')}_24_regular.svg"
        url = f"{source}/assets/{quote(folder)}/SVG/{filename}"
        response = session.get(url, timeout=30)
        response.raise_for_status()
        svg = response.text
        (dest / f"{name}.svg").write_text(svg, encoding="utf-8")
        for variant, color in (("light", "#075BFF"), ("dark", "#85B8FF")):
            colored = svg.replace('fill="#212121"', f'fill="{color}"')
            (dest / f"{name}-{variant}.png").write_bytes(resvg_py.svg_to_bytes(svg_string=colored, width=72, height=72))
        records.append({"name": name, "source": url, "sha256": hashlib.sha256(response.content).hexdigest()})
    license_response = session.get(f"{source}/LICENSE", timeout=30)
    license_response.raise_for_status()
    (ROOT / "Fluent-Icons-LICENSE.txt").write_text(license_response.text, encoding="utf-8")
    (ROOT / "icon-sources.json").write_text(json.dumps({"repository": "microsoft/fluentui-system-icons", "commit": commit, "icons": records}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
