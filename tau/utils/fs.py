"""Small, dependency-free filesystem helpers shared across the codebase."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a text file atomically using a temporary sibling.

    Writes to a temp file in the same directory, fsyncs, then os.replace()s
    over the target — a crash or kill mid-write leaves either the old file
    or the new one intact, never a truncated/corrupted result. Callers
    rewriting a whole file in place (not appending) should use this instead
    of Path.write_text(), which truncates the target immediately and writes
    into it directly — an interrupted write there loses the file's prior
    content, not just the pending change.
    """
    # Follow symlinks to the file they name. os.replace() renames ONTO the path
    # it is given, so writing to a symlink replaced the link itself with a
    # regular file and left the real file untouched — the change looked applied
    # and landed nowhere. Symlinked config, a linked package in a monorepo, or
    # anything under /tmp on macOS (itself a symlink) hit this.
    #
    # Resolved before the temp file is created, so the temp sibling lands beside
    # the REAL target and stays on its filesystem, which os.replace() requires.
    with suppress(OSError):  # a circular or unreadable link keeps the raw path
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
