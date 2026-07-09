"""Tests for tau.tui.utils.strip_control_chars.

Session-picker and pending-queue previews truncate arbitrary user message
text to a single display line; a raw control character (a lone ESC not part
of a well-formed sequence, or any other C0/C1 byte) reaching that preview can
corrupt the picker's rendering instead of just showing as visible garbage.
"""

from __future__ import annotations

from tau.tui.utils import strip_control_chars


def test_replaces_newlines_and_tabs():
    assert strip_control_chars("a\nb\tc") == "a b c"


def test_replaces_lone_escape_byte():
    assert strip_control_chars("hello\x1bworld") == "hello world"


def test_replaces_other_c0_control_bytes():
    assert strip_control_chars("a\x01\x07\x0cb") == "a   b"


def test_replaces_c1_control_bytes():
    assert strip_control_chars("a\x9bb") == "a b"


def test_leaves_printable_text_untouched():
    assert strip_control_chars("hello world 123 日本語") == "hello world 123 日本語"


def test_custom_replacement():
    assert strip_control_chars("a\nb", replacement="") == "ab"
