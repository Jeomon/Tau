"""Regression: Terminal.read_raw must decode UTF-8 incrementally.

A per-chunk ``bytes.decode("utf-8", errors="replace")`` mangles any multibyte
sequence that straddles a read-chunk boundary into U+FFFD replacement
characters (reproduced with >64 bytes of CJK paste). The decoder must persist
across reads so a split character is reassembled on the next chunk.
"""

from __future__ import annotations

import os
import sys

from tau.tui.terminal import Terminal


class _FakeStdin:
    def fileno(self) -> int:
        return 0


def test_multibyte_sequence_split_across_reads(monkeypatch):
    term = Terminal()
    encoded = "中".encode()
    chunks = [encoded[:2], encoded[2:] + "文".encode()]
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr(os, "read", lambda fd, n: chunks.pop(0))

    assert term.read_raw() == ""  # incomplete tail held back, not replaced
    assert term.read_raw() == "中文"  # reassembled on the next chunk


def test_long_cjk_paste_survives_chunking(monkeypatch):
    term = Terminal()
    text = "多字节字符跨越读取边界不能损坏" * 4
    encoded = text.encode()
    chunks = [encoded[i : i + 64] for i in range(0, len(encoded), 64)]
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr(os, "read", lambda fd, n: chunks.pop(0))

    decoded = "".join(term.read_raw() for _ in range(len(chunks)))
    assert decoded == text
    assert "�" not in decoded


def test_invalid_bytes_are_still_replaced(monkeypatch):
    term = Terminal()
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr(os, "read", lambda fd, n: b"\xff")

    assert term.read_raw() == "�"
