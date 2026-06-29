from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tau.engine.types import AbortSignal

_locks: dict[Path, tuple[asyncio.Lock, int]] = {}
_registration_lock = asyncio.Lock()


def bounded_text_tail(
    text: str,
    *,
    max_bytes: int,
    max_lines: int,
) -> tuple[str, bool]:
    """Return a UTF-8-safe text tail bounded by lines and encoded bytes."""
    lines = text.splitlines(keepends=True)
    truncated = len(lines) > max_lines
    bounded = "".join(lines[-max_lines:])
    encoded = bounded.encode("utf-8")
    if len(encoded) > max_bytes:
        truncated = True
        encoded = encoded[-max_bytes:]
        while encoded and (encoded[0] & 0xC0) == 0x80:
            encoded = encoded[1:]
        bounded = encoded.decode("utf-8", errors="replace")
    return bounded, truncated


def human_size(size: int) -> str:
    """Convert a byte count to compact binary units."""
    value = size
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value //= 1024
    return f"{value:.1f}TB"


@asynccontextmanager
async def serialize_file_mutation(path: Path) -> AsyncIterator[None]:
    """Serialize mutations targeting the same resolved path."""
    key = path.resolve()
    async with _registration_lock:
        lock, users = _locks.get(key, (asyncio.Lock(), 0))
        _locks[key] = (lock, users + 1)
    try:
        async with lock:
            yield
    finally:
        async with _registration_lock:
            current_lock, users = _locks[key]
            if users == 1:
                _locks.pop(key)
            else:
                _locks[key] = (current_lock, users - 1)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a text file atomically using a temporary sibling."""
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


async def run_bounded_lines(
    command: Sequence[str],
    *,
    max_lines: int,
    signal: AbortSignal | None = None,
) -> tuple[int, list[str], bool]:
    """Run a subprocess, retaining at most max_lines plus one truncation sentinel."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    lines: list[str] = []
    cancelled = False
    try:
        while True:
            if signal is not None and signal.is_set():
                cancelled = True
                break
            read_task = asyncio.create_task(process.stdout.readline())
            signal_task = asyncio.create_task(signal.wait()) if signal is not None else None
            waiters: set[asyncio.Task[Any]] = {read_task}
            if signal_task is not None:
                waiters.add(signal_task)
            done, pending = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if signal_task is not None and signal_task in done:
                cancelled = True
                read_task.cancel()
                break
            data = read_task.result()
            if not data:
                break
            lines.append(data.decode("utf-8", errors="replace").rstrip("\r\n"))
            if len(lines) > max_lines:
                break
    finally:
        if process.returncode is None and (cancelled or len(lines) > max_lines):
            process.kill()
        await process.wait()
    return process.returncode or 0, lines, cancelled
