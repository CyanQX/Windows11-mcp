"""Compare actual frozen owned bytecode with the checked-out source, not labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import marshal
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    if isinstance(value, types.CodeType):
        fields = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals", "co_stacksize", "co_flags", "co_code", "co_consts", "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_firstlineno", "co_linetable", "co_exceptiontable")
        return {name: canonical(getattr(value, name)) for name in fields}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return [canonical(item) for item in value]
    if isinstance(value, frozenset):
        return {"frozenset": sorted((canonical(item) for item in value), key=repr)}
    if value is Ellipsis:
        return {"ellipsis": True}
    if isinstance(value, complex):
        return {"complex": [value.real, value.imag]}
    return value


def verify(executable: Path):
    reader = CArchiveReader(str(executable))
    pyz = reader.open_embedded_archive("PYZ.pyz")
    checked, mismatch = [], []
    for directory, prefix in ((ROOT / "installer", "installer"), (ROOT / "src" / "windows_mcp", "windows_mcp")):
        for path in directory.rglob("*.py"):
            relative = path.relative_to(directory).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts.pop()
            name = ".".join([prefix, *parts])
            if name == "installer.app":
                frozen = marshal.loads(reader.extract("app"))
            elif name in pyz.toc:
                frozen = pyz.extract(name)
            elif path.name == "__init__.py" and not path.read_text(encoding="utf-8").strip():
                continue  # Empty namespace package needs no executable code.
            else:
                mismatch.append(name + " (missing)")
                continue
            source = compile(path.read_bytes(), frozen.co_filename, "exec", dont_inherit=True, optimize=0)
            if canonical(source) != canonical(frozen):
                mismatch.append(name)
            checked.append(name)
    if mismatch:
        raise AssertionError("Frozen bytecode differs from current source: " + ", ".join(mismatch))
    return {"executable": executable.name, "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "owned_modules_checked": len(checked), "frozen_bytecode_matches_source": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.executable), indent=2))


if __name__ == "__main__":
    main()
