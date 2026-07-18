"""Content-based per-line anchor hashes shared by the read and edit tools.

Both tools must compute the exact same hash for the exact same line, since
``edit`` re-derives anchors from a fresh read of the file rather than trusting
any state carried over from a prior ``read`` call. Keeping the algorithm in
one place is what keeps them in agreement.
"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from tau.engine.types import AbortSignal

HASH_LEN = 4
# Astronomically unlikely to matter for any real file (65536 buckets per
# retry round), but bounds the loop instead of spinning forever in a
# pathological worst case (e.g. a file that is mostly one repeated line,
# longer than the entire hash space).
_MAX_RETRIES = 4096


def _base_hash(content: str, retry: int) -> str:
    basis = content if retry == 0 else f"{content}\x00{retry}"
    return hashlib.md5(basis.encode()).hexdigest()[:HASH_LEN]


def compute_line_hashes(lines: list[str]) -> list[str]:
    """Return one anchor hash per line, unique within this file (perfect hashing).

    The base hash is ``md5(stripped content)[:4]`` — identical to a plain
    per-line hash for the common case of non-repeated content, so most lines
    get the same anchor a naive per-line hash would produce. When a line's
    base hash collides with one already assigned to an earlier line in this
    file, the hash is recomputed with an increasing retry suffix until a free
    slot is found, so every line — including blank lines and repeated
    boilerplate like ``}`` or ``import os`` — gets its own distinct anchor.
    This removes any need to break ties by line-number proximity when
    resolving an anchor back to a line.
    """
    assigned: set[str] = set()
    hashes: list[str] = []
    for line in lines:
        content = line.strip()
        if not content:
            # Blank lines carry no content to hash meaningfully, but still
            # need a unique anchor like any other line — chain off a fixed
            # marker instead of the (also blank) stripped content.
            content = "\x00blank"
        retry = 0
        h = _base_hash(content, retry)
        while h in assigned and retry < _MAX_RETRIES:
            retry += 1
            h = _base_hash(content, retry)
        assigned.add(h)
        hashes.append(h)
    return hashes


_locks: dict[Path, tuple[asyncio.Lock, int]] = {}
_registration_lock = asyncio.Lock()


@dataclass(frozen=True)
class OutputSnapshot:
    """Bounded display output plus full-output spill metadata."""

    content: str
    total_bytes: int
    truncated: bool
    full_output_path: str | None


class OutputAccumulator:
    """Accumulate streamed bytes while retaining a bounded UTF-8 display tail.

    Raw output is written to a temporary file from the start. The file is
    deleted when output remains within the display bounds and preserved when
    truncation occurs.
    """

    def __init__(
        self,
        *,
        max_bytes: int,
        max_lines: int,
        temp_file_prefix: str,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_lines = max_lines
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._tail = ""
        self._truncated = False
        self._total_bytes = 0
        fd, temp_name = tempfile.mkstemp(prefix=temp_file_prefix, suffix=".log")
        self._stream: BinaryIO | None = os.fdopen(fd, "wb")
        self._path = Path(temp_name)
        self._finished = False

    def __enter__(self) -> OutputAccumulator:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the spill file descriptor, discarding the file if unfinished.

        Idempotent. If ``finish()`` already ran, the file was disposed of
        according to truncation and this is a no-op. Otherwise no snapshot ever
        escaped, so the fd is closed and the temp file removed.
        """
        if self._finished:
            return
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._path.unlink(missing_ok=True)
        self._finished = True

    def append(self, data: bytes) -> None:
        """Append one raw subprocess output chunk."""
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")
        if not data:
            return
        self._total_bytes += len(data)
        assert self._stream is not None
        self._stream.write(data)
        decoded = self._decoder.decode(data)
        self._tail, truncated = bounded_text_tail(
            self._tail + decoded,
            max_bytes=self._max_bytes,
            max_lines=self._max_lines,
        )
        self._truncated = self._truncated or truncated

    def snapshot(self) -> OutputSnapshot:
        """Return the current bounded output and spill-file metadata."""
        return OutputSnapshot(
            content=self._tail,
            total_bytes=self._total_bytes,
            truncated=self._truncated,
            full_output_path=str(self._path) if self._truncated else None,
        )

    def finish(self) -> OutputSnapshot:
        """Flush decoding and close or remove the spill file."""
        if self._finished:
            return self.snapshot()
        final_text = self._decoder.decode(b"", final=True)
        if final_text:
            self._tail, truncated = bounded_text_tail(
                self._tail + final_text,
                max_bytes=self._max_bytes,
                max_lines=self._max_lines,
            )
            self._truncated = self._truncated or truncated
        assert self._stream is not None
        self._stream.flush()
        self._stream.close()
        self._stream = None
        self._finished = True
        if not self._truncated:
            self._path.unlink(missing_ok=True)
        return self.snapshot()


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


_BINARY_SNIFF_BYTES = 8192


def looks_like_binary(data: bytes) -> bool:
    """Heuristically detect binary content from a leading sample of file bytes.

    A null byte essentially never appears in genuine UTF-8 text but is common
    in binary formats (images, archives, compiled objects), so its presence in
    the sampled prefix is a reliable, cheap signal — the same heuristic Git and
    most text editors use.
    """
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


def detect_image_mime(data: bytes) -> str | None:
    """Return the MIME type if ``data`` starts with a recognized image magic number.

    Unlike ``tau.message.utils.detect_image_mime``, this never guesses — it
    returns ``None`` for anything that isn't unambiguously PNG/JPEG/GIF/WEBP,
    so callers can tell "this is an image" apart from "this is some other
    binary format" (e.g. a zip, a compiled object) instead of mislabeling
    every non-text file as a PNG.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_tool_path(raw_path: str, cwd: Path | None) -> Path:
    """Resolve a tool's ``path`` argument against the invocation's working directory.

    Mirrors how ``grep``/``glob`` resolve their ``path`` argument: a relative
    value is joined to ``cwd`` (the agent's working directory) rather than
    Tau's own process working directory, so a relative path behaves the same
    regardless of which directory Tau itself was launched from.
    """
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (cwd or Path.cwd()) / path


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
        stdin=asyncio.subprocess.DEVNULL,
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
            try:
                done, _pending = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if signal_task is not None and signal_task in done:
                    cancelled = True
                    break
                data = read_task.result()
            finally:
                # Cancel and await every waiter, not just the ones asyncio.wait()
                # reported as still pending — cancelling a task without awaiting
                # it leaves it dangling until the GC reaps it (with a "Task was
                # destroyed but it is pending" warning) instead of actually
                # unwinding it now. Mirrors the read loop in
                # builtins/tools/terminal.py, which this was missing relative to.
                for task in waiters:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
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
