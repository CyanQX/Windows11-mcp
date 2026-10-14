"""Scoped text files, with read-back verification and no silent overwrite."""

from __future__ import annotations

from windows_mcp.engine.contracts import ActionResult, Status, Verification


def run(policy, operation: str, path: str, *, content: str | None = None, overwrite: bool = False) -> dict:
    destination = policy.check_path(path)
    if operation == "read":
        if destination.stat().st_size > 1_000_000:
            raise ValueError("Native text reads are limited to 1 MB")
        return {"source": "native", "text": destination.read_text(encoding="utf-8")}
    if operation == "info":
        return {"source": "native", "exists": destination.exists(), "is_file": destination.is_file(),
                "size": destination.stat().st_size if destination.exists() else None}
    if operation != "write" or content is None:
        raise ValueError("Native filesystem supports read, info, write; write requires content")
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Native text writes are limited to 1 MB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w" if overwrite else "x", encoding="utf-8", newline="") as file:
        file.write(content)
    passed = destination.read_bytes().decode("utf-8") == content
    return ActionResult(status=Status.VERIFIED if passed else Status.DISPATCHED_UNVERIFIED,
                        method="native_filesystem_write", precondition_passed=True, input_dispatched=True,
                        verification=Verification(passed=passed, evidence_type="file_content", detail="Written file content was independently read back")).model_dump()
