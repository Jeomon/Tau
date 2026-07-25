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
# How far a duplicated line's neighbourhood is widened before falling back to an
# ordinal. Each extra radius costs one more hash per duplicated line and
# separates nothing at all in a periodic file, so the budget is deliberately
# small.
MAX_RADIUS = 6
# Anchor values per line. A 4-hex token holds 65,536 values, so in a 70,000-line
# file distinct lines MUST share one and no scheme can address them all. Rather
# than promise global uniqueness and then refuse the whole file, the token widens
# only for files that need it: ordinary files keep 4 characters. 64 values per
# line keeps the collision rate under ~1%.
LOAD_FACTOR = 64
MAX_HASH_LEN = 8

_BOF = "\x00bof"
_EOF = "\x00eof"
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _content(line: str) -> str:
    """Whitespace-insensitive content of a line.

    Stripping is what lets a re-indented block keep its anchors. It also means
    two lines differing only in indentation share one content key.
    """
    return line.strip() or "\x00blank"


def anchor_width(n_lines: int) -> int:
    """Token width for a file of this many lines.

    Public because ``edit``'s anchor schema has to admit every width this can
    return — see ``EditParams``.
    """
    width = HASH_LEN
    while width < MAX_HASH_LEN and 16**width < LOAD_FACTOR * n_lines:
        width += 1
    return width


def _hash(blob: str, width: int) -> str:
    return hashlib.md5(blob.encode()).hexdigest()[:width]


def _token(line: str, width: int) -> str:
    """Tier-0 token: hash of the line's content alone."""
    return _hash(_content(line), width)


def _salted(lines: list[str], i: int, radius: int, width: int) -> str:
    """Tier-1 token: content plus the ``radius`` lines ABOVE it.

    Upward-only on purpose. Context *below* a line is the fragile half — append
    to the file or delete the line underneath and a symmetric salt changes even
    though the anchored line never moved. Everything above the line had to be
    read to reach it anyway, and a copy inserted elsewhere still lands in a
    different upward context, so position-independence survives.
    """
    parts = [f"\x00u{radius}"]
    for off in range(-radius, 1):
        j = i + off
        if j < 0:
            parts.append(_BOF)
        elif j >= len(lines):
            parts.append(_EOF)
        else:
            parts.append(_content(lines[j]))
    return _hash("\x00".join(parts), width)


def _b36(n: int) -> str:
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = _B36[r] + out
        if not n:
            return out


def stamp_lines(lines: list[str]) -> list[str]:
    """Return one anchor token per line, using two-tier salting.

    ``tier 0`` A line whose content is unique in the file keeps the plain
        content hash. The common case pays nothing, in width or in stability.

    ``tier 1`` A line whose content is duplicated is salted with its
        NEIGHBOURS, widening the radius until the copies separate. Neighbour
        salting is position-INDEPENDENT, which is the whole point: inserting a
        copy of a line elsewhere in the file leaves the original's token
        untouched.

    ``tier 2`` What even a wide neighbourhood cannot separate — a true run of
        identical lines in identical surroundings — falls back to an ordinal
        suffix. Only those lines pay extra width.

    This replaces a scheme that made anchors unique by salting every occurrence
    after the first with a retry counter. That guaranteed uniqueness *within one
    read* but not stability *across* edits, which is what hashline exists to
    provide: because the first occurrence held the unsalted token, inserting a
    copy above an anchored line handed the copy that token and silently
    relabelled the original. ``edit`` then found exactly one match, saw no
    ambiguity, and edited the decoy. See ``test_copy_inserted_above_*``.

    Because a line's tier depends on the file's own duplicate structure, an
    anchor can be stamped in one tier and read back in another. ``resolve_anchor``
    therefore matches every token a line *could* have carried, not just the one
    it carries now.
    """
    return _stamp(lines, anchor_width(len(lines)))


def _stamp(lines: list[str], width: int) -> list[str]:
    tokens = [_token(line, width) for line in lines]

    groups: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        groups.setdefault(_content(line), []).append(i)

    for members in groups.values():
        if len(members) == 1:
            continue  # tier 0: unique content, plain hash
        pending = members
        for radius in range(1, MAX_RADIUS + 1):
            salts = {i: _salted(lines, i, radius, width) for i in pending}
            seen: dict[str, int] = {}
            for salt in salts.values():
                seen[salt] = seen.get(salt, 0) + 1
            still: list[int] = []
            for i in pending:
                if seen[salts[i]] == 1:
                    tokens[i] = salts[i]  # tier 1: neighbours separate it
                else:
                    still.append(i)
            if len(still) == len(pending):
                # This radius separated nothing. In a periodic file — the same
                # call repeated every N lines — no wider radius will either, so
                # stop paying for hashes that cannot help.
                break
            pending = still
            if not pending:
                break
        if pending:
            # tier 2: identical lines in identical surroundings. Nothing about
            # the content can tell them apart, so pay a suffix.
            for ordinal, i in enumerate(members, start=1):
                if i in pending:
                    tokens[i] = _token(lines[i], width) + _b36(ordinal)

    # tier 1b: two DIFFERENT lines can still land on one token by hash
    # collision. They are not copies, so no later stage could separate them —
    # salt them with their upward context here instead. Without this, a
    # collision is accepted as an exact match and the edit lands on the wrong
    # line (the failure oh-my-pi hit in its 16-bit snapshot tags).
    used: dict[str, int] = {}
    for tok in tokens:
        used[tok] = used.get(tok, 0) + 1
    for i, tok in enumerate(tokens):
        if used.get(tok, 0) < 2:
            continue
        for radius in range(1, MAX_RADIUS + 1):
            cand = _salted(lines, i, radius, width)
            if cand not in used:
                used[tok] -= 1
                used[cand] = 1
                tokens[i] = cand
                break
    return tokens


def _candidate_tokens(lines: list[str], i: int, width: int) -> set[str]:
    """Every token line ``i`` could carry, across all tiers.

    A line's tier depends on how many copies of it the file holds, and that can
    change between the read and the edit. Matching only the current tier would
    throw away a perfectly good anchor whenever a copy appeared or vanished.
    """
    out = {_token(lines[i], width)}
    for radius in range(1, MAX_RADIUS + 1):
        out.add(_salted(lines, i, radius, width))
    return out


def resolve_anchor(
    current: list[str],
    anchor: str,
    hint: int,
    snapshot: list[str] | None = None,
) -> int | None:
    """Find the 0-based index in ``current`` that ``anchor`` refers to.

    Args:
        current: the file as it is now.
        anchor: the token hash the caller is holding (no line-number prefix).
        hint: the 1-based line number the anchor was displayed at.
        snapshot: the file as the reader saw it, when the caller kept one.
            Optional — ``edit`` has no such record today, and the scheme is
            designed to work without it.

    Returns:
        The line index, or None to refuse. Refusing costs the caller a re-read;
        resolving to the wrong line silently corrupts a file, so every ambiguous
        fork below refuses rather than guesses.
    """
    if not current:
        return None

    # The file's own size fixes the width. An anchor may nonetheless have been
    # stamped at a different width — the file crossed a threshold, or it carries
    # a tier-2 ordinal suffix and so is longer than the hash — so plausible
    # alternatives are tried only if the natural width finds nothing.
    natural = anchor_width(len(current))
    widths = [natural]
    for guess in (len(anchor), len(anchor) - 1, len(anchor) - 2):
        if HASH_LEN <= guess <= MAX_HASH_LEN and guess not in widths:
            widths.append(guess)

    for width in widths:
        got = _resolve_at(current, anchor, hint, width)
        if got is not None:
            return got
    return None


def _resolve_at(current: list[str], anchor: str, hint: int, width: int) -> int | None:
    """Resolve assuming the anchor was stamped with tokens ``width`` wide."""
    # 1. The token as the file stamps it today. Two-tier salting makes this
    #    unique for almost every line, with no snapshot needed.
    tokens = _stamp(current, width)
    exact = [i for i, tok in enumerate(tokens) if tok == anchor]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # Distinct lines sharing a token: a hash collision, not a duplicate.
        # Nothing here can separate them, so refuse.
        return None

    # 2. No line carries that token now. The anchor may have been stamped in a
    #    different tier — a copy of the line has appeared or been removed since
    #    the read. Look at every token each line could have carried.
    alt = [i for i in range(len(current)) if anchor in _candidate_tokens(current, i, width)]
    if not alt:
        return None
    if len(alt) == 1:
        return alt[0]

    # 3. Several candidates. Only tolerate that when they are literal copies of
    #    one another — then every candidate holds the same text, so the line
    #    number the anchor was displayed at picks the copy the caller meant.
    if len({_content(current[i]) for i in alt}) != 1:
        return None
    wanted = hint - 1
    return min(alt, key=lambda i: (abs(i - wanted), i))


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

# Formats whose ASCII preamble is long enough to hide the binary payload from
# the null-byte sniff below. A PDF's first null byte routinely lands well past
# 8 KiB (observed at 14k, 36k and 603k in real files) and sometimes never
# appears at all, so roughly one PDF in four is read as text without this.
# Deliberately short: zip, gzip, xz, bzip2, tar, ELF, Mach-O and PE all place a
# null byte within their first few bytes and are already caught, so listing
# them would add maintenance drift and catch nothing new.
_BINARY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "PDF"),
    (b"%!PS", "PostScript"),
)


def detect_binary_format(data: bytes) -> str | None:
    """Return a human-readable format name for known non-text magic numbers.

    Complements :func:`looks_like_binary`, which is a good general heuristic but
    structurally blind to formats that lead with ASCII. Naming the format also
    lets callers say "this is a PDF" instead of reporting some unrelated
    downstream symptom.
    """
    for magic, name in _BINARY_MAGIC:
        if data.startswith(magic):
            return name
    return None


def looks_like_binary(data: bytes) -> bool:
    """Heuristically detect binary content from a leading sample of file bytes.

    A null byte essentially never appears in genuine UTF-8 text but is common
    in binary formats (images, archives, compiled objects), so its presence in
    the sampled prefix is a reliable, cheap signal — the same heuristic Git and
    most text editors use. Measured across ~40k non-source files it missed
    nothing except the ASCII-preamble formats in :data:`_BINARY_MAGIC`; raising
    the sample size does not help, since those formats can contain no null byte
    whatsoever.
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
