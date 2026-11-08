"""Crash-safe file primitive tests."""

from __future__ import annotations

from pathlib import Path

from signals.fileio import atomic_write_text, file_lock


def test_atomic_write_replaces_content_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]


def test_file_lock_is_reentrant_across_sequential_uses(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    with file_lock(target):
        atomic_write_text(target, "locked write")
    with file_lock(target):
        assert target.read_text() == "locked write"
