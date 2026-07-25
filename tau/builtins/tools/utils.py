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
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from tau.engine.types import AbortSignal
from tau.utils.fs import atomic_write_text  # noqa: F401 — re-exported for write.py/edit.py

HASH_LEN = 4
# Every distinct anchor is HASH_LEN hex digits, so the whole space holds
# 16**4 = 65536 anchors. HASH_LEN is not free to change: the edit tool's
# schema pins anchors to exactly four characters (``pattern=r"^\d+:.{4}$"``),
# so widening the hash would invalidate the tool contract and every anchor
# the model is currently holding.
_HASH_SPACE = 16**HASH_LEN
# Truncated md5 over successive retry values is not a bijection, so probing
# fills the space like a coupon-collector draw rather than a permutation:
# reaching 50% load takes ~46k probes, 95% ~197k, and 100% ~668k. The ceiling
# therefore has to sit well above _HASH_SPACE or a legitimately anchorable
# file would be refused. Probing is amortized per content, so this is a total
# budget for the file (~0.5s in the pathological all-identical case), not a
# per-line one.
_MAX_PROBES = _HASH_SPACE * 16


class AnchorSpaceExhausted(RuntimeError):
    """Raised when unique anchors cannot be assigned to every line of a file.

    With only ``_HASH_SPACE`` distinct anchors available, a file longer than
    that cannot get one anchor per line — by the pigeonhole principle, not by
    bad luck. Callers must surface this as a refusal rather than proceeding:
    duplicate anchors make ``edit`` ambiguous, and a wrong-but-plausible
    anchor resolution silently edits the wrong line.
    """


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

    Probing resumes from where the same content left off rather than
    restarting at retry 0. For a line repeated ``k`` times, retries 0..k-2 are
    necessarily already taken by that content's own earlier occurrences (the
    assigned set only ever grows, so a slot is never freed), which made the
    naive rescan re-derive the same doomed hashes over and over —
    O(k^2) work for k duplicates. Resuming yields byte-identical anchors for
    a fraction of the hashing: a 9k-line file of repetitive generated code
    drops from ~540 ms to ~7 ms, and a pathological 570k-line input from an
    effectively unbounded ~2 billion hashes to a prompt refusal.

    Raises:
        AnchorSpaceExhausted: If the file cannot be assigned unique anchors.
    """
    if len(lines) > _HASH_SPACE:
        raise AnchorSpaceExhausted(
            f"{len(lines)} lines exceeds the {_HASH_SPACE} available anchors; "
            "this file cannot be safely anchored for editing."
        )

    assigned: set[str] = set()
    # Highest retry already consumed per content, so occurrence k starts
    # probing past occurrence k-1 instead of rescanning from zero.
    next_retry: dict[str, int] = {}
    hashes: list[str] = []
    for line in lines:
        content = line.strip()
        if not content:
            # Blank lines carry no content to hash meaningfully, but still
            # need a unique anchor like any other line — chain off a fixed
            # marker instead of the (also blank) stripped content.
            content = "\x00blank"
        retry = next_retry.get(content, 0)
        h = _base_hash(content, retry)
        while h in assigned:
            retry += 1
            if retry >= _MAX_PROBES:
                # Unreachable while the length guard above holds, but a
                # duplicate anchor must never be emitted silently.
                raise AnchorSpaceExhausted(
                    f"No free anchor for line content {content[:40]!r} after "
                    f"{retry} probes; the anchor space is effectively full."
                )
            h = _base_hash(content, retry)
        assigned.add(h)
        next_retry[content] = retry + 1
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


async def run_bounded_lines(
    command: Sequence[str],
    *,
    max_lines: int,
    signal: AbortSignal | None = None,
    timeout: float | None = None,
) -> tuple[int, list[str], bool, bool]:
    """Run a subprocess, retaining at most max_lines plus one truncation sentinel.

    ``max_lines`` only bounds output *size* — without ``timeout``, a subprocess
    that hangs (a pathologically slow search over a huge/network-mounted tree,
    a search tool stuck reading a special file) blocks forever with no
    automatic cutoff, unlike builtins/tools/terminal.py's bash tool, which has
    always had one. ``signal`` remains the *user*-triggered abort path (Escape/
    Ctrl+C); ``timeout`` is the automatic one, matching terminal.py's
    ``timed_out``/``cancelled`` split so callers can tell the two apart.

    Returns ``(returncode, lines, cancelled, timed_out)``.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    lines: list[str] = []
    cancelled = False
    timed_out = False

    async def _read_loop() -> None:
        nonlocal cancelled
        assert process.stdout is not None
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

    try:
        try:
            if timeout is not None:
                await asyncio.wait_for(_read_loop(), timeout=timeout)
            else:
                await _read_loop()
        except TimeoutError:
            timed_out = True
        except asyncio.CancelledError:
            # Task cancellation is distinct from the cooperative AbortSignal.
            # Mark it before cleanup so the child cannot outlive its caller.
            cancelled = True
            raise
    finally:
        # EOF on stdout does not guarantee that the child exited: a process can
        # close or redirect its output and keep running. Bound that final wait
        # too; otherwise this helper (and every parallel grep/glob batch using
        # it) can remain pending forever despite its advertised timeout.
        if process.returncode is None and not (cancelled or timed_out or len(lines) > max_lines):
            try:
                if timeout is None:
                    await process.wait()
                else:
                    await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                timed_out = True

        if process.returncode is None and (cancelled or timed_out or len(lines) > max_lines):
            with suppress(ProcessLookupError):
                process.kill()

        # Killing normally reaps immediately, but cleanup itself must never
        # turn a bounded search into an unbounded await.
        if process.returncode is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=1.0)
    return process.returncode if process.returncode is not None else -1, lines, cancelled, timed_out
