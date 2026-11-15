"""Shared writes preserve prior data and other writers' files on failure."""
import secrets

import pytest

from windows_mcp.infrastructure import local_storage


def test_atomic_write_preserves_existing_temporary_files(tmp_path, monkeypatch):
    # The previous helper truncated/deleted this sibling on a candidate collision.
    monkeypatch.setattr(secrets, "token_hex", lambda _: "fixed")
    target = tmp_path / "state.json"
    other_writer = target.with_suffix(".fixed.tmp")
    other_writer.write_bytes(b"another writer's pending data")
    local_storage.atomic_write_bytes(target, b"new state", fsync=True)
    assert target.read_bytes() == b"new state"
    assert other_writer.read_bytes() == b"another writer's pending data"
    assert set(tmp_path.iterdir()) == {target, other_writer}


@pytest.mark.parametrize("failed_operation", ["fsync", "replace"])
def test_failed_atomic_write_keeps_original_and_removes_only_own_temporary_file(tmp_path, monkeypatch, failed_operation):
    target = tmp_path / "state.json"
    target.write_bytes(b"original state")
    personal = tmp_path / "personal.tmp"
    personal.write_bytes(b"keep")

    def fail(*_):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(local_storage.os, failed_operation, fail)
    with pytest.raises(OSError, match="simulated storage failure"):
        local_storage.atomic_write_bytes(target, b"uncommitted state", fsync=True)
    assert target.read_bytes() == b"original state"
    assert personal.read_bytes() == b"keep"
    assert set(tmp_path.iterdir()) == {target, personal}
