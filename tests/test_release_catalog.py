"""Profile/feature tables must not be mistaken for the release's MCP tool catalog."""

import pytest

from scripts.verify_distribution import documented_tools


def test_release_catalog_ignores_profile_and_other_tables():
    readme = """## 功能简介
| `full` | 31 |
| `browser` | 5 |
## 工具一览
| `Observe` | Read controls |
| `Execute` | Act |
## Other
| `Example` | Not a tool |
"""
    assert documented_tools(readme) == {"Observe", "Execute"}


def test_release_requires_an_explicit_tool_catalog():
    with pytest.raises(AssertionError, match="catalog is missing"):
        documented_tools("| `full` | 31 |")


def test_release_catalog_can_end_at_eof_without_trailing_newline():
    assert documented_tools("## 工具一览\n| `Observe` | Controls |") == {"Observe"}
