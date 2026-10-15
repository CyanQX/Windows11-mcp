"""Patch one JSON/JSONC/JSON5 object member without reserializing other members."""

from __future__ import annotations

import json
import re

import json5

TOKEN = re.compile(r'''\s+|//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{}\[\],:]|[^\s{}\[\],:]+''')


def patch_member(source: str, path: list[str], value) -> str:
    source = source or "{}"
    tokens = [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(source) if not m.group().isspace() and not m.group().startswith(("//", "/*"))]

    def end(index):
        if tokens[index][0] not in ("{", "["):
            return index + 1
        opener = tokens[index][0]
        closer = "}" if opener == "{" else "]"
        index += 1
        while tokens[index][0] != closer:
            index = end(index) if tokens[index][0] in ("{", "[") else index + 1
        return index + 1

    def members(index):
        if tokens[index][0] != "{":
            raise ValueError("Target JSON member is not an object")
        result = []
        index += 1
        while tokens[index][0] != "}":
            key = tokens[index][0]
            name = json5.loads(key) if key.startswith(('"', "'")) else key
            if tokens[index + 1][0] != ":":
                raise ValueError("Invalid JSON member")
            finish = end(index + 2)
            comma = finish if tokens[finish][0] == "," else None
            result.append((name, index, index + 2, finish, comma))
            index = finish + 1 if comma is not None else finish
        return result, index

    def formatted(item, key_start):
        if "\n" not in source:
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        beginning = source.rfind("\n", 0, key_start) + 1
        indentation = re.match(r"[ \t]*", source[beginning:]).group()
        return json.dumps(item, ensure_ascii=False, indent=2).replace("\n", "\n" + indentation)

    def edit(index, remaining):
        rows, close = members(index)
        match = next((row for row in rows if row[0] == remaining[0]), None)
        if match is not None:
            _, key, start, finish, comma = match
            if len(remaining) > 1:
                return edit(start, remaining[1:])
            first, last = tokens[start][1], tokens[finish - 1][2]
            if value is not None:
                return source[:first] + formatted(value, tokens[key][1]) + source[last:]
            # Keep comments intact. A preceding comma is removed separately if needed.
            if comma is not None:
                return source[:tokens[key][1]] + source[tokens[comma][2]:]
            previous = rows[rows.index(match) - 1] if rows.index(match) else None
            if previous is not None and previous[4] is not None:
                comma_start, comma_end = tokens[previous[4]][1:]
                return source[:comma_start] + source[comma_end:tokens[key][1]] + source[last:]
            return source[:tokens[key][1]] + source[last:]
        if value is None:
            return source
        nested = value
        for name in reversed(remaining[1:]):
            nested = {name: nested}
        close_at = tokens[close][1]
        parent_start = source.rfind("\n", 0, tokens[index][1]) + 1
        parent_indent = re.match(r"[ \t]*", source[parent_start:]).group()
        indent = parent_indent + "  "
        encoded = json.dumps(nested, ensure_ascii=False, indent=2).replace("\n", "\n" + indent)
        insertion = "\n" + indent + json.dumps(remaining[0], ensure_ascii=False) + ": " + encoded + "\n" + parent_indent
        prefix = source[:close_at]
        if rows and rows[-1][4] is None:
            comma_at = tokens[rows[-1][3] - 1][2]
            prefix = source[:comma_at] + "," + source[comma_at:close_at]
        return prefix + insertion + source[close_at:]

    return edit(0, path)
