"""Crash-safe file primitives shared by every JSON-backed store.

Two stdlib-only helpers, kept in this leaf package so every layer above
can use them without breaking the layering rule:

  - `atomic_write_text(path, text)` — write to a sibling temp file, fsync,
    then `os.replace` it over the target. Readers see either the old file
    or the new one, never a truncated half-write.
  - `file_lock(path)` — advisory exclusive lock on `<path>.lock` held for
    the duration of a read-modify-write, so the engine and a CLI command
    upserting the same JSON file can't silently drop each other's rows.

`fcntl` is POSIX-only; without it the lock degrades to a no-op while the
atomic replace still holds.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX platforms
    fcntl = None  # type: ignore[assignment]


def atomic_write_text(path: Path, text: str) -> None:
    """Replace `path` with `text` atomically (same-directory temp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on `<path>.lock` for the block."""
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
