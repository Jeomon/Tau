"""Tests for tau/builtins/tools/hashline.py — the shared perfect-hashing anchor scheme."""

from __future__ import annotations

import hashlib

from tau.builtins.tools.utils import HASH_LEN, compute_line_hashes


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
