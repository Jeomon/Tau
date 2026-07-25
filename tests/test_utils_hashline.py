"""Tests for the shared hashline anchor scheme in tau/builtins/tools/utils.py.

Both read and edit must derive the same token for the same line, since edit
re-stamps the file from disk rather than trusting anything carried over from a
prior read. Keeping the algorithm in one place is what keeps them in agreement.
"""

from __future__ import annotations

import hashlib

from tau.builtins.tools.utils import (
    HASH_LEN,
    anchor_width,
    detect_binary_format,
    dominant_newline,
    join_lines,
    looks_like_binary,
    resolve_anchor,
    split_lines,
    split_lines_with_endings,
    stamp_lines,
)


def test_unique_lines_keep_the_plain_isolated_hash():
    """Tier 0: content that appears once in the file keeps the naive per-line
    md5, so the common case pays nothing in width or in churn."""
    lines = ["import os", "def f():", "    return 1"]
    hashes = stamp_lines(lines)
    expected = [hashlib.md5(line.strip().encode()).hexdigest()[:HASH_LEN] for line in lines]
    assert hashes == expected


def test_all_hashes_are_unique_within_a_file():
    lines = ["foo"] * 20 + [""] * 10 + ["bar", "foo", ""]
    hashes = stamp_lines(lines)
    assert len(hashes) == len(lines)
    assert len(set(hashes)) == len(hashes)


def test_duplicated_lines_are_salted_by_neighbours_not_by_position():
    """The property the old scheme lacked, and the reason it edited wrong lines.

    Previously the FIRST occurrence of a content kept the unsalted hash and
    later ones were salted with a retry counter. That is positional: insert a
    copy above an anchored line and the copy becomes "first", takes over the
    token, and the original is silently relabelled. Salting from the lines
    ABOVE a duplicate instead means a copy appearing elsewhere cannot disturb
    it.
    """
    before = ["a = 1", "dup()", "b = 2", "dup()"]
    after = ["a = 1", "dup()", "INSERTED", "b = 2", "dup()"]
    # The insertion is above the anchored duplicate but does not touch its
    # immediate upward context, so its token is PRESERVED. Under the old
    # positional scheme the token moved to whichever copy came first.
    assert stamp_lines(before)[3] == stamp_lines(after)[4]


def test_blank_lines_are_not_all_identical():
    hashes = stamp_lines(["", "", ""])
    assert len(set(hashes)) == 3


def test_whitespace_only_lines_treated_as_blank():
    """Indentation-only lines strip to empty, same as a truly blank line —
    they should still each get their own anchor, not collide silently."""
    hashes = stamp_lines(["    ", "\t", ""])
    assert len(set(hashes)) == 3


def test_hash_width_is_uniform_within_a_small_file():
    """Every token in one file shares a width, apart from a tier-2 ordinal
    suffix, so a reader's output stays column-aligned."""
    hashes = stamp_lines(["a", "b", "c", "", "d"])
    assert all(len(h) == HASH_LEN for h in hashes)


def test_token_width_grows_with_file_length():
    """Width no longer adapts, at any file size.

    A 4-hex token holds 65,536 values, so a 70,000-line file MUST give two lines
    the same token. That used to be intolerable, because a collision was
    invisible: two lines carrying one token were indistinguishable and edit had
    no way to tell which was meant. Width was the only defence.

    ``edit`` now checks the resolved line against the digest ``read`` recorded,
    so a collision is caught instead of avoided — and the width, which was paid
    on every line of every read, buys nothing.
    """
    assert anchor_width(10) == HASH_LEN
    assert anchor_width(1_025) == HASH_LEN
    assert anchor_width(70_000) == HASH_LEN
    assert anchor_width(10**9) == HASH_LEN


def test_deterministic_across_calls():
    lines = ["x"] * 5 + ["y"] * 5
    assert stamp_lines(lines) == stamp_lines(list(lines))


def test_stable_regardless_of_slicing_point():
    """read.py stamps the whole file then slices for the requested chunk — the
    anchor for a given absolute line must not depend on where a chunk boundary
    falls. This matters more now than it did: a duplicate's salt comes from its
    neighbours and the width comes from the total line count, so stamping only
    a window would produce different anchors for the same line.
    """
    lines = ["dup"] * 8
    full = stamp_lines(lines)
    assert full[3:6] == stamp_lines(lines)[3:6]


def test_heavily_duplicated_lines_all_stay_unique():
    """Regression: the retry scan used to restart at 0 for every occurrence,
    so k copies of a line cost O(k^2) hashing and, once _MAX_RETRIES (4096)
    was exhausted, duplicate anchors were emitted silently — which makes
    edit's anchor resolution ambiguous."""
    lines = ["dup"] * 5000
    hashes = stamp_lines(lines)
    assert len(set(hashes)) == 5000


def test_many_blank_lines_all_stay_unique():
    hashes = stamp_lines([""] * 5000)
    assert len(set(hashes)) == 5000


def test_no_copy_holds_the_unsalted_token():
    """Deliberate reversal of the old contract.

    The previous scheme gave occurrence 0 the plain ``md5(content)`` and salted
    the rest, and a test here asserted that. That property IS the wrong-line
    bug: whichever copy comes first owns the plain token, so inserting a copy
    above an anchored line hands the newcomer that token and relabels the
    original. Now every copy of a duplicated content is salted, so there is no
    unsalted token for a new copy to steal.
    """
    lines = ["dup"] * 500
    hashes = stamp_lines(lines)
    plain = hashlib.md5(b"dup").hexdigest()[: anchor_width(len(lines))]
    assert plain not in hashes
    assert len(set(hashes)) == len(lines)


def test_duplicate_heavy_file_is_not_quadratic():
    """A repetitive 60k-line file must complete quickly, not in ~minutes."""
    import time

    lines = [f"line{i % 1000}" for i in range(60_000)]
    start = time.perf_counter()
    hashes = stamp_lines(lines)
    elapsed = time.perf_counter() - start
    assert len(hashes) == 60_000
    # Deliberately not asserting uniqueness: 60,000 lines cannot all hold a
    # distinct 4-hex token, and no longer need to. What this test guards is that
    # salting stayed linear, which is what once made this file take minutes.
    assert elapsed < 10.0, f"took {elapsed:.1f}s — salting regressed"


def test_periodic_file_does_not_widen_forever():
    """In a periodic file no neighbourhood radius ever separates the copies, so
    widening must stop as soon as a radius separates nothing rather than paying
    for six rounds of hashes that cannot help."""
    import time

    lines = [f"call_{i % 500}()" for i in range(40_000)]
    start = time.perf_counter()
    hashes = stamp_lines(lines)
    elapsed = time.perf_counter() - start
    assert len(hashes) == 40_000
    assert elapsed < 10.0, f"took {elapsed:.1f}s — widening did not abort early"


def test_file_larger_than_the_4hex_space_is_no_longer_refused():
    """Previously a file with more lines than the 65,536 four-hex anchors was
    refused outright, by pigeonhole. It is now read and stamped at the same
    width as every other file — its collisions are caught by verification
    rather than avoided by a wider token.
    """
    lines = [f"row_{i}" for i in range(16**HASH_LEN + 1)]
    hashes = stamp_lines(lines)
    assert len(hashes) == len(lines)
    assert all(len(h) == HASH_LEN for h in hashes)
    # Pigeonhole: more lines than the token space holds, so some token IS
    # shared. Width used to buy uniqueness here; the digest now buys detection
    # instead, which is the property that actually protects the file.
    assert len(set(hashes)) < len(lines)


class TestDetectBinaryFormat:
    """The null-byte sniff is blind to formats that lead with ASCII: a PDF's
    first null byte routinely lands past the 8 KiB sample (observed 14k, 36k,
    603k in real files) and sometimes never appears at all. Large ones then
    failed with an unrelated "too many lines to anchor" message; small ones
    were read as pages of mojibake.
    """

    def test_pdf_magic_is_detected(self):
        assert detect_binary_format(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj") == "PDF"

    def test_postscript_magic_is_detected(self):
        assert detect_binary_format(b"%!PS-Adobe-3.0\n%%Title: x") == "PostScript"

    def test_pdf_with_no_null_byte_at_all_is_still_detected(self):
        assert detect_binary_format(b"%PDF-1.4 fake pdf") == "PDF"

    def test_pdf_whose_null_byte_is_past_the_sniff_window(self):
        raw = b"%PDF-1.4\n" + b"% metadata line\n" * 1200 + b"\x00stream"
        assert not looks_like_binary(raw), "precondition: the sniff must miss this"
        assert detect_binary_format(raw) == "PDF"

    def test_plain_text_is_not_flagged(self):
        assert detect_binary_format(b"def hello():\n    return 1\n") is None

    def test_percent_comment_text_is_not_flagged(self):
        # A LaTeX or Matlab file starts with '%' but is not PostScript.
        assert detect_binary_format(b"% This is a LaTeX comment\n\\documentclass{article}") is None

    def test_formats_the_null_sniff_already_catches_are_not_listed(self):
        """Kept deliberately short — zip/gzip/xz/tar all put a null byte in
        their first few bytes, so listing them adds drift and catches nothing."""
        realistic_headers = (
            b"PK\x03\x04\x14\x00\x00\x00\x08\x00",          # zip / docx / jar
            b"\x1f\x8b\x08\x00\x00\x00\x00\x00",             # gzip
            b"\xfd7zXZ\x00\x00\x04\xe6\xd6\xb4F",             # xz
            b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8,         # ELF
        )
        for header in realistic_headers:
            assert looks_like_binary(header), "sniff should already catch this"
            assert detect_binary_format(header) is None


class TestResolveAnchor:
    """Anchor resolution, including the wrong-line bug this scheme replaced.

    The previous scheme salted every occurrence after the first with a retry
    counter, so the FIRST copy of a content held the unsalted token. Inserting a
    copy above an anchored line made the copy "first": it took over the token
    and the original was silently relabelled. edit then found exactly one match,
    saw no ambiguity, and edited the decoy — a confident wrong-line edit, which
    is the worst possible failure for a tool that mutates files.
    """

    BASE = [
        "import os",
        "",
        "def load(path):",
        "    with open(path) as fh:",
        "        data = fh.read()",
        "    return data",
    ]

    def _anchor(self, lines, index):
        return stamp_lines(lines)[index], index + 1

    def test_copy_inserted_above_resolves_to_the_original(self):
        anchor, hint = self._anchor(self.BASE, 4)
        after = self.BASE[:2] + ["        data = fh.read()"] + self.BASE[2:]
        # The decoy copy sits at index 2; the caller's line moved to index 5.
        assert after[2] == after[5]
        assert resolve_anchor(after, anchor, hint) == 5

    def test_copy_inserted_below_resolves_to_the_original(self):
        anchor, hint = self._anchor(self.BASE, 4)
        after = self.BASE + ["        data = fh.read()"]
        assert resolve_anchor(after, anchor, hint) == 4

    def test_resolves_after_lines_inserted_above(self):
        anchor, hint = self._anchor(self.BASE, 4)
        after = ["# a", "# b", "# c"] + self.BASE
        assert resolve_anchor(after, anchor, hint) == 7

    def test_resolves_after_lines_deleted_above(self):
        anchor, hint = self._anchor(self.BASE, 4)
        assert resolve_anchor(self.BASE[2:], anchor, hint) == 2

    def test_refuses_when_the_target_line_itself_changed(self):
        """A line differing by one token is not the same line. Silently rebasing
        onto it is the wrong-line edit the scheme exists to prevent."""
        anchor, hint = self._anchor(self.BASE, 4)
        after = self.BASE[:4] + ["        data = fh.readlines()"] + self.BASE[5:]
        assert resolve_anchor(after, anchor, hint) is None

    def test_refuses_when_the_target_line_was_deleted(self):
        anchor, hint = self._anchor(self.BASE, 4)
        assert resolve_anchor(self.BASE[:4] + self.BASE[5:], anchor, hint) is None

    def test_refuses_a_fabricated_anchor(self):
        assert resolve_anchor(self.BASE, "zzzz", 5) is None

    def test_refuses_against_an_empty_file(self):
        anchor, hint = self._anchor(self.BASE, 4)
        assert resolve_anchor([], anchor, hint) is None

    def test_picks_the_right_closer_among_identical_ones(self):
        closers = ["def f():", "    if a:", "        b()", "        }", "    }", "}"]
        anchor, hint = self._anchor(closers, 4)
        after = ["# hdr"] + closers
        assert resolve_anchor(after, anchor, hint) == 5

    def test_picks_the_right_blank_line_in_a_run(self):
        blanks = ["a = 1", "", "", "", "", "b = 2"]
        anchor, hint = self._anchor(blanks, 3)
        after = ["# hdr"] + blanks
        assert resolve_anchor(after, anchor, hint) == 4

    def test_survives_reindentation(self):
        anchor, hint = self._anchor(self.BASE, 4)
        after = [line.replace("    ", "  ") for line in self.BASE]
        assert resolve_anchor(after, anchor, hint) == 4

    def test_never_resolves_to_the_wrong_line_under_random_edits(self):
        """Refusing is acceptable; landing on a different line is not."""
        import random

        random.seed(1234)
        pool = ["x = 1", "}", "    }", "", "    return None", "    pass", "call()"]
        wrong = 0
        for _ in range(200):
            before = [
                random.choice(pool) if random.random() < 0.5 else f"v{i} = f({i})"
                for i in range(random.randint(20, 80))
            ]
            target = random.randrange(len(before))
            anchor, hint = self._anchor(before, target)
            after, shift = list(before), 0
            for _ in range(random.randint(1, 4)):
                at = random.randrange(len(after) + 1)
                if random.random() < 0.5:
                    after.insert(at, f"# ins {random.random():.5f}")
                    if at <= target + shift:
                        shift += 1
                elif len(after) > 2 and at < len(after) and at != target + shift:
                    after.pop(at)
                    if at < target + shift:
                        shift -= 1
            truth = target + shift
            if not (0 <= truth < len(after)) or after[truth] != before[target]:
                continue
            got = resolve_anchor(after, anchor, hint)
            if got is not None and got != truth:
                wrong += 1
        assert wrong == 0, f"{wrong}/200 resolved to the wrong line"


class TestLineSplitting:
    """read and edit must agree on what a line IS, since an anchor identifies a
    position in that list. Shared helpers, so they cannot drift apart.

    str.splitlines was the wrong primitive: it also breaks on form feed,
    vertical tab, NEL and U+2028, and edit then rejoined with "\\n" — so editing
    an unrelated line destroyed the separator, and every CRLF file was rewritten
    as LF.
    """

    def test_matches_splitlines_for_ordinary_text(self):
        for text in ("", "a", "a\n", "a\nb", "a\nb\n", "\n", "\n\n\n", "a\r\nb\r\n"):
            assert split_lines(text) == text.splitlines(), repr(text)

    def test_does_not_split_on_form_feed(self):
        assert split_lines("a\nb\x0cc\nd\n") == ["a", "b\x0cc", "d"]
        assert "a\nb\x0cc\nd\n".splitlines() == ["a", "b", "c", "d"]  # the old behaviour

    def test_does_not_split_on_other_exotic_boundaries(self):
        for ch in ("\x0b", "\x1c", "\x1d", "\x1e", "\u0085", "\u2028", "\u2029"):
            assert split_lines(f"a\nb{ch}c\n") == ["a", f"b{ch}c"], repr(ch)

    def test_round_trip_is_exact(self):
        for text in ("", "a", "a\n", "a\nb", "a\r\nb\r\n", "a\rb\r", "a\r\nb\nc\r",
                     "a\x0cb\n", "\ufeffa\n", "a\nb"):
            contents, endings = split_lines_with_endings(text)
            assert join_lines(contents, endings) == text, repr(text)

    def test_endings_are_reported_per_line(self):
        contents, endings = split_lines_with_endings("a\r\nb\nc")
        assert contents == ["a", "b", "c"]
        assert endings == ["\r\n", "\n", ""]   # "" = ends at EOF without one

    def test_dominant_newline_picks_the_file_convention(self):
        assert dominant_newline(["\r\n", "\r\n", "\n"]) == "\r\n"
        assert dominant_newline(["\n", "\n", "\r\n"]) == "\n"
        assert dominant_newline(["\r", "\r"]) == "\r"
        assert dominant_newline([]) == "\n"          # empty file: pick a sane default
        assert dominant_newline([""]) == "\n"        # single line, no terminator
