"""README.md is the single user document: keep it (and manifest.json) in sync with the code."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


class FakeMCP:
    def __init__(self):
        self.options: dict[str, dict] = {}

    def tool(self, *, name, **kwargs):
        self.options[name] = kwargs
        return lambda func: func


def registered_tools() -> dict[str, dict]:
    from windows_mcp.tools import register_all

    mcp = FakeMCP()
    register_all(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    return mcp.options


def _section(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## )", README, flags=re.S | re.M)
    assert match, f"README section '## {title}' is missing"
    return match.group(1)


def test_tool_table_matches_the_registered_tools():
    documented = set(re.findall(r"^\| `([A-Za-z]+)` \|", _section("工具一览"), flags=re.M))
    assert documented == set(registered_tools())


def test_tool_counts_in_the_readme_are_true():
    tools = registered_tools()

    def is_read_only(annotations) -> bool:  # mcp 2 renamed readOnlyHint -> read_only_hint
        value = getattr(annotations, "read_only_hint", None)
        return bool(annotations.readOnlyHint if value is None else value)

    read_only = sum(1 for options in tools.values() if is_read_only(options["annotations"]))
    match = re.search(r"共 (\d+) 个工具：(\d+) 个只读、(\d+) 个会改变系统状态", README)
    assert match, "the README must state the tool counts"
    assert tuple(map(int, match.groups())) == (len(tools), read_only, len(tools) - read_only)


def test_every_environment_variable_the_code_reads_is_documented():
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))
    used = set(re.findall(r"\b((?:WINDOWS_MCP|POSTHOG)_[A-Z_]+|ANONYMIZED_TELEMETRY)\b", source))
    used.add("WINDOWS_MCP_DESKTOP_TESTS")  # read by tests/desktop/conftest.py
    missing = sorted(name for name in used if f"`{name}`" not in README)
    assert missing == [], f"undocumented environment variables: {missing}"


def _slug(heading: str) -> str:
    text = heading.strip().lower().replace("`", "")
    return "".join(ch for ch in text if ch.isalnum() or ch in "-_ ").replace(" ", "-")


def test_internal_links_point_to_existing_headings():
    body = re.sub(r"```.*?```", "", README, flags=re.S)
    slugs = {_slug(m.group(1)) for m in re.finditer(r"^#{2,6} (.+)$", body, flags=re.M)}
    broken = [anchor for anchor in re.findall(r"\]\(#([^)]+)\)", body) if anchor not in slugs]
    assert broken == []


def test_readme_is_the_only_user_document():
    markdown = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in ROOT.rglob("*.md")
        if not any(part.startswith(".venv") or part == "node_modules" for part in p.parts)
    )
    assert markdown == [".claude/skills/windows-mcp-tool-tester/SKILL.md", "CLAUDE.md", "LICENSE.md", "README.md"]


def test_manifest_lists_every_tool_and_matches_the_package_version():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert {tool["name"] for tool in manifest["tools"]} == set(registered_tools())
    assert manifest["version"] == project["version"]
    assert manifest["user_config"]["anonymized_telemetry"]["default"] is False
