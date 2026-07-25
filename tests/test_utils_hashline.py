"""Tests for tau/builtins/tools/hashline.py — the shared perfect-hashing anchor scheme."""

from __future__ import annotations

import hashlib

import pytest

from tau.builtins.tools.utils import (
    HASH_LEN,
    AnchorSpaceExhausted,
    compute_line_hashes,
    detect_binary_format,
    looks_like_binary,
)


def test_unique_lines_keep_the_plain_isolated_hash():
    """The common case (no collision) must match a naive per-line md5 hash,
    so anchors don't change unnecessarily for the vast majority of files."""
    lines = ["import os", "def f():", "    return 1"]
    hashes = compute_line_hashes(lines)
    expected = [hashlib.md5(line.strip().encode()).hexdigest()[:HASH_LEN] for line in lines]
    assert hashes == expected


def test_all_hashes_are_unique_within_a_file():
    lines = ["foo"] * 20 + [""] * 10 + ["bar", "foo", ""]
    hashes = compute_line_hashes(lines)
    assert len(hashes) == len(lines)
    assert len(set(hashes)) == len(hashes)


def test_first_occurrence_keeps_base_hash_later_ones_differ():
    hashes = compute_line_hashes(["foo", "foo", "foo"])
    base = hashlib.md5(b"foo").hexdigest()[:HASH_LEN]
    assert hashes[0] == base
    assert hashes[1] != base
    assert hashes[2] != base
    assert hashes[1] != hashes[2]


def test_blank_lines_are_not_all_identical():
    hashes = compute_line_hashes(["", "", ""])
    assert len(set(hashes)) == 3


def test_whitespace_only_lines_treated_as_blank():
    """Indentation-only lines strip to empty, same as a truly blank line —
    they should still each get their own anchor, not collide silently."""
    hashes = compute_line_hashes(["    ", "\t", ""])
    assert len(set(hashes)) == 3


def test_hash_length_is_stable():
    hashes = compute_line_hashes(["a", "a", "a", "", "b"])
    assert all(len(h) == HASH_LEN for h in hashes)


def test_deterministic_across_calls():
    lines = ["x"] * 5 + ["y"] * 5
    assert compute_line_hashes(lines) == compute_line_hashes(list(lines))


def test_stable_regardless_of_slicing_point():
    """read.py hashes the whole file then slices for the requested chunk —
    the hash for a given absolute line must not depend on where a chunk
    boundary happens to fall."""
    lines = ["dup"] * 8
    full = compute_line_hashes(lines)
    assert full[3:6] == compute_line_hashes(lines)[3:6]


def test_heavily_duplicated_lines_all_stay_unique():
    """Regression: the retry scan used to restart at 0 for every occurrence,
    so k copies of a line cost O(k^2) hashing and, once _MAX_RETRIES (4096)
    was exhausted, duplicate anchors were emitted silently — which makes
    edit's anchor resolution ambiguous."""
    lines = ["dup"] * 5000
    hashes = compute_line_hashes(lines)
    assert len(set(hashes)) == 5000


def test_many_blank_lines_all_stay_unique():
    hashes = compute_line_hashes([""] * 5000)
    assert len(set(hashes)) == 5000


def test_first_occurrence_still_plain_hash_after_many_duplicates():
    """The perf fix must not shift anchors: occurrence 0 of any content keeps
    the naive md5 anchor no matter how many duplicates follow it."""
    lines = ["dup"] * 5000
    assert compute_line_hashes(lines)[0] == hashlib.md5(b"dup").hexdigest()[:HASH_LEN]


def test_duplicate_heavy_file_is_not_quadratic():
    """A repetitive 60k-line file must complete quickly, not in ~minutes."""
    import time

    lines = [f"line{i % 1000}" for i in range(60_000)]
    start = time.perf_counter()
    hashes = compute_line_hashes(lines)
    elapsed = time.perf_counter() - start
    assert len(set(hashes)) == 60_000
    assert elapsed < 5.0, f"took {elapsed:.1f}s — collision probing regressed to quadratic"


def test_file_longer_than_the_anchor_space_is_refused():
    """More lines than distinct anchors is unsatisfiable by pigeonhole, so it
    must raise rather than hand back duplicate anchors."""
    with pytest.raises(AnchorSpaceExhausted):
        compute_line_hashes(["x"] * (16**HASH_LEN + 1))


def test_exactly_the_anchor_space_still_works():
    hashes = compute_line_hashes(["y"] * (16**HASH_LEN))
    assert len(set(hashes)) == 16**HASH_LEN


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
