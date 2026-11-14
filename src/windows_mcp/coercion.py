"""Argument coercion shared by the tools, the desktop layer and the policy gate.

Some MCP clients (Claude Desktop in particular) serialise booleans and lists as strings, so a
tool parameter can arrive as ``"true"`` or ``"[100, 200]"`` instead of its declared type.
"""

from __future__ import annotations

import json
from typing import Any


def truthy(value: Any) -> bool:
    """Lenient flag: only ``True`` and the string ``"true"`` (any case) are true.

    Everything else, including malformed text, is false. Use :func:`parse_bool` when a bad value
    should be reported instead.
    """
    return value is True or (isinstance(value, str) and value.lower() == "true")


def parse_bool(value: bool | str, name: str) -> bool:
    """Strict flag: a real boolean or the string ``"true"``/``"false"``; anything else raises."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be true or false")


def json_list(value: list | str | None) -> list | None:
    """Coerce a JSON-stringified list (``'[100, 200]'``) back to a list.

    Claude Desktop strips ``anyOf`` schemas and the model serialises lists as strings; parsing
    here keeps the tools working.
    """
    if value is None or isinstance(value, list):
        return value
    return json.loads(value)
