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
import re
import tempfile
from collections import OrderedDict
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
# Extra radii used only to break a hash collision between two tokens, past the
# range ordinary salting already covers.
FIXUP_RADII = 4

_BOF = "\x00bof"
_EOF = "\x00eof"


# Real line terminators, and nothing else. ``str.splitlines`` also breaks on
# \x0b, \x0c, \x1c-\x1e, \x85, \u2028 and \u2029, so a file using form feeds as
# page separators (older C, Lisp and Emacs-era sources) was split at them and
# then rejoined with "\n" — silently destroying the separator when an unrelated
# line was edited. Splitting only here keeps a form feed inside its line.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def split_lines(text: str) -> list[str]:
    """Split text into lines on real terminators only, dropping the terminators.

    Shared by ``read`` and ``edit`` on purpose: an anchor identifies a position
    in this list, so the two tools have to agree on what a line *is*. Matches
    ``str.splitlines`` for ordinary text and differs only for the exotic
    boundaries noted above.
    """
    parts = _LINE_BREAK.split(text)
    if parts and parts[-1] == "":
        parts.pop()  # a trailing terminator does not start a new line
    return parts


def split_lines_with_endings(text: str) -> tuple[list[str], list[str]]:
    """Return ``(contents, terminators)``, one terminator per line.

    The terminator is ``""`` for a final line that ends at EOF without one.
    ``edit`` needs these to write the file back with the endings it came with:
    normalising CRLF to LF turns a one-line change into a whole-file diff on a
    Windows checkout.
    """
    contents: list[str] = []
    endings: list[str] = []
    pos = 0
    for match in _LINE_BREAK.finditer(text):
        contents.append(text[pos : match.start()])
        endings.append(match.group())
        pos = match.end()
    if pos < len(text):
        contents.append(text[pos:])
        endings.append("")
    return contents, endings


def dominant_newline(endings: list[str]) -> str:
    """The terminator a new line in this file should use."""
    counts: dict[str, int] = {}
    for end in endings:
        if end:
            counts[end] = counts.get(end, 0) + 1
    if not counts:
        return "\n"
    return max(counts, key=lambda k: counts[k])


def join_lines(contents: list[str], endings: list[str]) -> str:
    """Inverse of :func:`split_lines_with_endings`."""
    return "".join(content + end for content, end in zip(contents, endings, strict=True))

def _content(line: str) -> str:
    """Whitespace-insensitive content of a line.

    Stripping is what lets a re-indented block keep its anchors. It also means
    two lines differing only in indentation share one content key.
    """
    return line.strip() or "\x00blank"


# Characters of content digest retained per displayed line, so ``edit`` can check
# that the line an anchor resolved to still says what ``read`` showed there.
#
# A digest held in STATE is a different object from a digest held in the ANCHOR.
# Widening the anchor buys nothing once the whole token matches — any
# content-derived part of it matches too. This digest is never compared against
# the anchor at all; it is compared against the line the anchor landed on, which
# is why it detects the one failure the token cannot describe.
#
# Two characters, not one: a 1-hex digest lets one wrong line in sixteen through
# (measured 93.79% detection against 93.75% analytic), while 2 hex lets through
# one in 256 (measured 99.43%). Neither is perfect and the third character is not
# worth its byte, but the second removes a hole large enough to hit in practice.
DIGEST_CHARS = 2

# Paths whose digests are retained. Each entry is DIGEST_CHARS bytes per line, so
# 64 files of 10,000 lines is ~1.3 MB — nothing beside a session transcript.
_DIGEST_PATHS = 64

# Keyed by resolved path, module-level rather than on the tool instance: TOOLS
# holds singletons, but create_read_tool()/create_edit_tool() hand fresh
# instances to extensions, and a read through one of those must still be visible
# to the editor.
_digests: OrderedDict[Path, str] = OrderedDict()


def _digest(line: str) -> str:
    """Content digest of one line, taken from the TAIL of the hash.

    The tail, because the anchor token is the HEAD of the same digest. Sharing
    characters would make this partly redundant with the token it exists to
    check — a line that collides on the token would then be more likely to
    collide here too, which is precisely backwards.
    """
    return hashlib.md5(_content(line).encode()).hexdigest()[-DIGEST_CHARS:]


def _digest_key(path: Path) -> Path:
    """Canonical store key for a file.

    Symlinks are resolved, because the tools disagree about how to spell a path
    and the store must not: ``glob`` and ``grep`` return fully resolved paths
    while ``resolve_tool_path`` does not, and on macOS ``/tmp`` and ``/var`` are
    themselves symlinks. Reading a file via a grep result and then editing it via
    the path the user typed would otherwise look like two different files, and
    the edit would be refused for want of a record that exists under another
    name.

    ``serialize_file_mutation`` already keys its lock this way; this makes the
    digest store agree with it.
    """
    try:
        return path.resolve()
    except OSError:  # pragma: no cover — unreadable parent, keep the raw path
        return path


def record_digests(path: Path, lines: list[str]) -> None:
    """Retain what ``read`` displayed, for a later ``edit`` to verify against.

    Stored as one flat string indexed by arithmetic rather than a dict: a dict of
    {int: str} costs roughly 100 bytes an entry in CPython, about 1 MB for a
    10,000-line file, against 20 KB for the string.

    The whole file is digested, not just the requested window. ``read`` already
    stamps the whole file — anchors must not depend on the window — and an edit
    may carry an anchor from a different read of the same file.
    """
    key = _digest_key(path)
    _digests[key] = "".join(_digest(line) for line in lines)
    _digests.move_to_end(key)
    while len(_digests) > _DIGEST_PATHS:
        _digests.popitem(last=False)


def digest_at(path: Path, line_number: int) -> str | None:
    """The digest ``read`` recorded at this 1-based line, or None if there is none."""
    blob = _digests.get(_digest_key(path))
    if blob is None or line_number < 1:
        return None
    start = (line_number - 1) * DIGEST_CHARS
    return blob[start : start + DIGEST_CHARS] or None


def verify_resolved(path: Path, hint: int, line: str) -> bool | None:
    """Does ``line`` match what ``read`` showed at ``hint``?

    Returns None when nothing was recorded for that position — a different
    process, an evicted entry, or a restart. The caller must treat that as a
    refusal rather than a pass: an anchor can only have come from a read, so a
    missing digest means the evidence is gone, and there is no token width behind
    it to fall back on.
    """
    want = digest_at(path, hint)
    if want is None:
        return None
    return _digest(line) == want


# How far above the anchor the reader's neighbourhood is compared, when two
# content-identical candidates have to be told apart. Upward only: context BELOW
# an anchor near the top of a file always exists, so including it manufactures
# agreement exactly where the upward window is honestly truncated — measured to
# resolve 6 of 7 genuinely ambiguous cases, i.e. to guess.
CONTEXT_RADIUS = 3
# Lines that must agree, counted OUTWARD from the anchor and stopping at the
# first mismatch, before a candidate is believed. Capped by how many lines the
# reader actually saw, so it stays reachable at a file edge.
#
# 3 rather than 2 is a values choice with a measurement behind it. Over 800
# constructed twin situations, 2 resolves 10 more cases correctly and commits 2
# silent wrong edits; 3 gives up those 10 and commits none. Silent corruption is
# priced far above a wasted re-read here, and the choice holds for any weighting
# above ~5x.
CONTEXT_MIN_RUN = 3
# At least this many lines must be comparable at all, so that "every one of zero
# comparisons agreed" resolves nothing.
CONTEXT_MIN_COMPARABLE = 1


def digest_blob(path: Path) -> str | None:
    """The digests ``read`` retained for this file, or None."""
    return _digests.get(_digest_key(path))


def _by_context(
    current: list[str], candidates: list[int], hint: int, digests: str | None
) -> int | None:
    """Pick the candidate whose neighbourhood matches the one ``read`` displayed.

    Two lines with identical content cannot be told apart by their anchor, their
    token, or the digest of the line itself — every content-derived value agrees.
    What still differs is what sat AROUND them when the reader saw one of them,
    and ``read`` retained exactly that.

    Scored as an unbroken run of agreement counted outward from the anchor,
    stopping at the first mismatch. A run is used rather than a count because the
    insertion that created the twin necessarily perturbs distant context: the
    original of a copied block agrees immediately above and diverges further up,
    while a decoy diverges immediately.

    The winner must then beat every rival strictly; ties refuse. Dominance alone
    is not enough — with no run requirement, a candidate agreeing on one far line
    dominates one agreeing on none, which measured as resolving 187 of 199
    cases that no evidence settles.
    """
    if digests is None or not candidates:
        return None
    home = hint - 1
    deltas = sorted((d for d in range(-CONTEXT_RADIUS, 1) if d != 0), key=abs)

    best_score = -1
    best: list[int] = []
    for i in candidates:
        comparable = run = 0
        broken = False
        for delta in deltas:
            k, j = home + delta, i + delta
            expected = digest_at_index(digests, k)
            if expected is None:
                continue  # the reader saw nothing here: no evidence either way
            comparable += 1
            if broken:
                continue  # still comparable, but past the break
            actual = _digest(current[j]) if 0 <= j < len(current) else None
            if expected == actual:
                run += 1
            else:
                broken = True
        score = run
        if comparable < CONTEXT_MIN_COMPARABLE or run < min(CONTEXT_MIN_RUN, comparable):
            score = -1  # too little agreement beside the anchor to believe
        if score > best_score:
            best_score, best = score, [i]
        elif score == best_score:
            best.append(i)

    if best_score < 1:
        return None  # nothing agreed: no evidence for anyone
    if len(best) == 1:
        return best[0]
    return None  # the evidence does not separate them


def digest_at_index(digests: str, index: int) -> str | None:
    """Digest at a 0-based line index, or None if outside what was read."""
    if index < 0:
        return None
    start = index * DIGEST_CHARS
    return digests[start : start + DIGEST_CHARS] or None


def forget_digests(path: Path | None = None) -> None:
    """Drop retained digests — for tests, and for callers that rewrite a file."""
    if path is None:
        _digests.clear()
    else:
        _digests.pop(_digest_key(path), None)

def anchor_width(n_lines: int) -> int:
    """Token width for a file of this many lines — now always ``HASH_LEN``.

    This used to widen with the line count, and a duplicated line was widened
    one character further still. Both existed to make a token COLLISION rare,
    because a collision was undetectable: two lines carrying the same token were
    indistinguishable, so the only defence was to make the space large enough
    that it seldom happened.

    ``edit`` now verifies the resolved line against the digest ``read``
    recorded, which detects the collision instead of avoiding it — so the width
    buys nothing and is charged on every line of every read. A 4-hex token in a
    70,000-line file guarantees collisions; that is fine, because a collision is
    now caught rather than silently followed.

    Kept as a function, rather than inlining ``HASH_LEN``, because it is the one
    place this reasoning belongs and ``edit``'s schema refers to it.
    """
    return HASH_LEN


def _hash(blob: str, width: int) -> str:
    return hashlib.md5(blob.encode()).hexdigest()[:width]



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


def _runs(contents: list[str]) -> list[tuple[int, int]]:
    """For each line, ``(length of its maximal identical run, index within it)``.

    Computed once per stamp and reused, so binding the run into a token costs no
    more than a single linear pass.
    """
    out: list[tuple[int, int]] = [(1, 0)] * len(contents)
    start = 0
    for i in range(1, len(contents) + 1):
        if i == len(contents) or contents[i] != contents[start]:
            length = i - start
            for k in range(length):
                out[start + k] = (length, k)
            start = i
    return out


def _run_token(
    contents: list[str], i: int, run: tuple[int, int], radius: int, width: int
) -> str:
    """Token for a line inside a run of identical lines.

    Bound to the run's LENGTH as well as the line's index within it. Distance
    from the run's head is positional, and that is the whole problem: grow the
    run above the target and member k inherits member k-1's token, so a stale
    anchor resolves confidently one line short.

    Binding the length means any growth or shrinkage of the run invalidates
    every member's token instead — the anchor then matches nothing and
    ``resolve_anchor`` refuses. That refusal is the honest answer rather than a
    retreat: adding a copy *above* the target and adding one *below* it produce
    a byte-identical file, so which line the caller meant is not recoverable
    from the inputs at all. Two refusals are strictly better than one right
    answer and one silent wrong edit.
    """
    length, k = run
    head = i - k
    parts = [f"\x00run{radius}", str(length), str(k), contents[i]]
    for off in range(radius, 0, -1):
        j = head - off
        parts.append(contents[j] if j >= 0 else _BOF)
    return _hash("\x00".join(parts), width)


def _ordinal_token(content: str, count: int, ordinal: int, width: int) -> str:
    """Last resort for copies nothing else separates, e.g. a periodic file.

    The occurrence COUNT is bound in alongside the ordinal, so inserting or
    deleting a copy anywhere invalidates these anchors rather than silently
    shifting which copy each ordinal names. A plain hash rather than a suffix,
    so these lines no longer pay extra width either.
    """
    return _hash(f"\x00ord\x00{count}\x00{ordinal}\x00{content}", width)


def stamp_lines(lines: list[str]) -> list[str]:
    """Return one anchor token per line.

    ``tier 0`` Content unique in the file keeps the plain content hash. The
        common case pays nothing, in width or in stability.

    ``tier 1r`` A line inside a run of identical lines is named by its run's
        (length, index) plus the context above the run's HEAD — not by its own
        distance from that head, which is positional.

    ``tier 1`` An isolated duplicate is salted with the lines above it,
        widening the radius until the copies separate. Position-independent:
        inserting a copy elsewhere leaves the original's token untouched.

    ``tier 2`` Copies nothing else separates are named by ordinal with the
        occurrence count bound in, so the naming self-invalidates when the
        number of copies changes.

    ``tier 1b`` Two *different* lines colliding on one token are re-salted, so
        a collision is never accepted as an exact match.

    This replaces a scheme that salted every occurrence after the first with a
    retry counter. That guaranteed uniqueness *within one read* but not
    stability *across* edits, which is what hashline exists to provide: the
    first occurrence held the unsalted token, so inserting a copy above an
    anchored line handed the copy that token and silently relabelled the
    original. ``edit`` then found one match, saw no ambiguity, and edited the
    decoy.
    """
    return _stamp(lines, anchor_width(len(lines)))



# One edit stamps the same file up to three times — resolving the start anchor,
# resolving the end anchor, and building the near-miss table for an error
# message — and on a 100,000-line file that was 2.4 of 7.6 seconds spent
# recomputing an identical answer.
#
# Keyed on the CONTENT, not on the list's identity: a content key cannot go
# stale, because different content is a different key. id() would be both unsafe
# (ids are reused once a list is collected) and wrong (the same file re-read into
# a new list would miss).
#
# Two entries, because the access pattern is a burst of identical calls within
# one edit rather than reuse across files. A 100,000-line entry is a few MB; an
# ordinary file is tens of KB.
_STAMP_CACHE_ENTRIES = 2
_stamp_cache: OrderedDict[tuple[str, int], list[str]] = OrderedDict()


def _stamp(lines: list[str], width: int) -> list[str]:
    """Cached wrapper over ``_stamp_uncached``.

    Returns a copy, so a caller mutating the result cannot poison later reads of
    the same file — the one way a cache like this turns into a wrong-line edit.
    """
    # Lines never contain a newline (they were split on real terminators), so
    # joining with one is an unambiguous encoding of the list.
    key = (hashlib.md5("\n".join(lines).encode()).hexdigest(), width)
    hit = _stamp_cache.get(key)
    if hit is not None:
        _stamp_cache.move_to_end(key)
        return list(hit)
    tokens = _stamp_uncached(lines, width)
    _stamp_cache[key] = tokens
    while len(_stamp_cache) > _STAMP_CACHE_ENTRIES:
        _stamp_cache.popitem(last=False)
    return list(tokens)


def _stamp_uncached(lines: list[str], width: int) -> list[str]:
    contents = [_content(line) for line in lines]
    tokens = [_hash(c, width) for c in contents]
    runs = _runs(contents)

    groups: dict[str, list[int]] = {}
    for i, content in enumerate(contents):
        groups.setdefault(content, []).append(i)

    for content, members in groups.items():
        if len(members) == 1:
            continue  # tier 0: unique content, plain hash

        # tier 1r: members sitting inside a run are named by (run length, index
        # in run) plus context above the run's head.
        pending_runs = [i for i in members if runs[i][0] > 1]
        isolated = [i for i in members if runs[i][0] == 1]
        for radius in range(0, MAX_RADIUS + 1):
            if not pending_runs:
                break
            salts = {
                i: _run_token(contents, i, runs[i], radius, width) for i in pending_runs
            }
            seen: dict[str, int] = {}
            for salt in salts.values():
                seen[salt] = seen.get(salt, 0) + 1
            still = [i for i in pending_runs if seen[salts[i]] != 1]
            for i in pending_runs:
                if seen[salts[i]] == 1:
                    tokens[i] = salts[i]
            if len(still) == len(pending_runs):
                break  # widening separates nothing; stop paying for hashes
            pending_runs = still

        # tier 1: isolated copies keep the upward-context salt, which is stable
        # under edits anywhere else in the file.
        pending = isolated
        for radius in range(1, MAX_RADIUS + 1):
            if not pending:
                break
            salts = {i: _salted(lines, i, radius, width) for i in pending}
            seen = {}
            for salt in salts.values():
                seen[salt] = seen.get(salt, 0) + 1
            still = [i for i in pending if seen[salts[i]] != 1]
            for i in pending:
                if seen[salts[i]] == 1:
                    tokens[i] = salts[i]
            if len(still) == len(pending):
                # This radius separated nothing. In a periodic file — the same
                # call repeated every N lines — no wider radius will either.
                break
            pending = still

        leftover = pending + pending_runs
        if leftover:
            count = len(members)
            for ordinal, i in enumerate(members, start=1):
                if i in leftover:
                    tokens[i] = _ordinal_token(content, count, ordinal, width)

    # tier 1b: two DIFFERENT lines can land on one token by hash collision.
    # They are not copies, so no later stage could separate them.
    used: dict[str, int] = {}
    for tok in tokens:
        used[tok] = used.get(tok, 0) + 1
    for i, tok in enumerate(tokens):
        if used.get(tok, 0) < 2:
            continue
        # A run member is re-salted within the RUN family. A per-line salt would
        # put it back on "distance from the run head", which is precisely the
        # naming that lets member k inherit member k-1's token.
        if runs[i][0] > 1:
            forms = (
                _run_token(contents, i, runs[i], r, width)
                for r in range(MAX_RADIUS + 1, MAX_RADIUS + 1 + FIXUP_RADII)
            )
        else:
            forms = (_salted(lines, i, r, width) for r in range(1, MAX_RADIUS + 1))
        for cand in forms:
            if cand not in used:
                used[tok] -= 1
                used[cand] = 1
                tokens[i] = cand
                break
    return tokens


def resolve_anchor(
    current: list[str],
    anchor: str,
    hint: int,
    snapshot: list[str] | None = None,
    digests: str | None = None,
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

    # One width, because there is only one. When the width adapted to file
    # length an edit had to try the neighbouring widths too, in case the file had
    # crossed a threshold since the read — and every extra comparison set was
    # another lottery ticket for a dead anchor to hit live content.
    return _resolve_at(current, anchor, hint, HASH_LEN, digests)


def _resolve_at(
    current: list[str], anchor: str, hint: int, width: int, digests: str | None = None
) -> int | None:
    """Resolve assuming the anchor was stamped with tokens ``width`` wide."""
    # 1. The token as the file stamps it today. The tiers make this unique for
    #    almost every line, with no snapshot needed.
    tokens = _stamp(current, width)
    exact = [i for i, tok in enumerate(tokens) if tok == anchor]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # Distinct lines sharing a token: a hash collision, not a duplicate.
        # Nothing here can separate them, so refuse.
        return None

    # 2. No line carries that token now. The anchor may still belong to a line
    #    that was UNIQUE when it was read and has since acquired a twin: that
    #    line was stamped with the plain content hash, which is content-derived
    #    and stable, so following it is safe.
    #
    #    Only that one form is offered. Run members are excluded entirely:
    #    every extra form a dead anchor may match is another chance to follow
    #    it onto a live line.
    contents = [_content(line) for line in current]
    runs = _runs(contents)
    alt = [
        i
        for i, content in enumerate(contents)
        if runs[i][0] == 1 and _hash(content, width) == anchor
    ]
    if not alt:
        return None
    if len(alt) == 1:
        return alt[0]

    # 3. Several candidates, all literal copies of one content: the line number
    #    the anchor was displayed at picks the copy the caller meant. Inside a
    #    run this inference is unavailable, which is why those are excluded.
    # Several candidates, and no way to tell them apart. The line number the
    # anchor carries is NOT a way: it describes where the line sat in a file
    # that has since changed, so "nearest to the old position" is a guess. It
    # pays off when the copy appears near the anchor and loses when the file
    # shifted underneath it, and losing means editing the wrong line silently.
    #
    # Reproduced before this refusal was added: read a 4-line save(), anchor
    # its "return None", then add a load() helper ABOVE carrying an identical
    # line. The copy then sits nearer the old line number than the original
    # does, so the edit rewrote load() and left save() untouched, with no
    # error. The retained digest does not catch it either — both copies say the
    # same thing, and a digest settles what the line SAID, not which copy was
    # MEANT.
    #
    # The line number is not a way to choose, but the reader's NEIGHBOURHOOD is:
    # read retained a digest for every line it displayed, so the candidate whose
    # surroundings match what was actually seen can be identified on evidence
    # instead of on distance. Refuses when the evidence does not separate them.
    return _by_context(current, alt, hint, digests)


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
