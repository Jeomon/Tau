"""Tests for tau/modes/interactive/input_handler.py — paste marker round-trips.

Covers the [paste #N], [image/audio/video #N], and persistent [type:uuid]
marker contracts: insertion, expansion/extraction on submit, and rewriting
into history. Constructs a bare InputHandler (bypassing __init__, which needs
a live Runtime/Layout/TUI) and only sets the attributes each method touches.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tau.modes.interactive.input_handler import InputHandler


def make_handler() -> InputHandler:
    h = object.__new__(InputHandler)
    h._pasted_texts = {}
    h._paste_counter = 0
    h._clipboard_images = {}
    h._clipboard_image_notes = {}
    h._clipboard_image_counter = 0
    h._clipboard_audio = {}
    h._clipboard_audio_counter = 0
    h._clipboard_video = {}
    h._clipboard_video_counter = 0
    h._layout = MagicMock()
    h._layout.input.text = ""
    h._layout.input._cursor = 0
    h._tui = MagicMock()
    return h


class TestTextPasteMarker:
    def test_short_paste_is_inserted_verbatim(self):
        h = make_handler()
        h._on_paste_text("short text")
        h._layout.input.insert_at_cursor.assert_called_once_with("short text")
        assert h._pasted_texts == {}

    def test_long_paste_by_line_count_gets_marker(self):
        h = make_handler()
        text = "\n".join(f"line {i}" for i in range(50))
        h._on_paste_text(text)
        marker = h._layout.input.insert_at_cursor.call_args[0][0]
        assert marker == "[paste #1 +50 lines]"
        assert h._pasted_texts[1] == text

    def test_long_paste_by_char_count_gets_marker(self):
        h = make_handler()
        text = "x" * 2000  # single line, over _LARGE_PASTE_CHARS
        h._on_paste_text(text)
        marker = h._layout.input.insert_at_cursor.call_args[0][0]
        assert marker == f"[paste #1 {len(text)} chars]"

    def test_expand_round_trips_exact_text(self):
        h = make_handler()
        text = "\n".join(f"line {i}" for i in range(50))
        h._on_paste_text(text)
        marker = h._layout.input.insert_at_cursor.call_args[0][0]

        expanded = h._expand_pasted_texts(f"before {marker} after")
        assert expanded == f"before {text} after"

    def test_expand_clears_stash_after_use(self):
        h = make_handler()
        h._on_paste_text("\n".join(f"line {i}" for i in range(50)))
        marker = h._layout.input.insert_at_cursor.call_args[0][0]
        h._expand_pasted_texts(marker)
        assert h._pasted_texts == {}
        assert h._paste_counter == 0

    def test_expand_leaves_unknown_marker_untouched(self):
        h = make_handler()
        assert h._expand_pasted_texts("[paste #7 +3 lines]") == "[paste #7 +3 lines]"

    def test_multiple_pastes_get_distinct_indices(self):
        h = make_handler()
        text_a = "\n".join(f"a{i}" for i in range(20))
        text_b = "\n".join(f"b{i}" for i in range(20))
        h._on_paste_text(text_a)
        marker_a = h._layout.input.insert_at_cursor.call_args[0][0]
        h._on_paste_text(text_b)
        marker_b = h._layout.input.insert_at_cursor.call_args[0][0]

        assert marker_a != marker_b
        expanded = h._expand_pasted_texts(f"{marker_a} {marker_b}")
        assert expanded == f"{text_a} {text_b}"


class TestImagePasteMarker:
    def test_extract_reads_bytes_for_known_marker(self, tmp_path):
        h = make_handler()
        p = tmp_path / "img.png"
        p.write_bytes(b"PNGDATA")
        h._clipboard_images[1] = ("uuid-1", str(p))

        images, missing = h._extract_clipboard_images("see [image #1] here")
        assert images == [b"PNGDATA"]
        assert missing == 0

    def test_extract_deduplicates_repeated_marker(self, tmp_path):
        h = make_handler()
        p = tmp_path / "img.png"
        p.write_bytes(b"PNGDATA")
        h._clipboard_images[1] = ("uuid-1", str(p))

        images, missing = h._extract_clipboard_images("[image #1] and again [image #1]")
        assert images == [b"PNGDATA"]
        assert missing == 0

    def test_extract_skips_unknown_index(self):
        h = make_handler()
        images, missing = h._extract_clipboard_images("[image #99]")
        assert images == []
        assert missing == 0

    def test_extract_clears_state_after_use(self, tmp_path):
        h = make_handler()
        p = tmp_path / "img.png"
        p.write_bytes(b"PNGDATA")
        h._clipboard_images[1] = ("uuid-1", str(p))
        h._extract_clipboard_images("[image #1]")
        assert h._clipboard_images == {}
        assert h._clipboard_image_counter == 0

    def test_extract_persistent_uuid_marker_via_history_lookup(self, tmp_path, monkeypatch):
        h = make_handler()
        p = tmp_path / "old.png"
        p.write_bytes(b"OLDPNG")
        monkeypatch.setattr(h, "_find_media_by_uuid", lambda uid: p if uid == "abc" else None)

        images, missing = h._extract_clipboard_images("[image:abc]")
        assert images == [b"OLDPNG"]
        assert missing == 0

    def test_extract_persistent_uuid_marker_missing_file_counts_as_missing(self, monkeypatch):
        h = make_handler()
        monkeypatch.setattr(h, "_find_media_by_uuid", lambda _uid: None)

        images, missing = h._extract_clipboard_images("[image:gone]")
        assert images == []
        assert missing == 1


class TestAudioVideoPasteMarker:
    def test_extract_clipboard_audio_reads_bytes(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"AUDIODATA")
        h._clipboard_audio[1] = ("uuid-a", str(p))

        audio = h._extract_clipboard_audio("[audio #1]")
        assert audio == [b"AUDIODATA"]
        assert h._clipboard_audio == {}

    def test_extract_clipboard_video_reads_bytes(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp4"
        p.write_bytes(b"VIDEODATA")
        h._clipboard_video[1] = ("uuid-v", str(p))

        video = h._extract_clipboard_video("[video #1]")
        assert video == [b"VIDEODATA"]
        assert h._clipboard_video == {}

    def test_extract_audio_persistent_uuid_marker(self, tmp_path, monkeypatch):
        h = make_handler()
        p = tmp_path / "old.mp3"
        p.write_bytes(b"OLDAUDIO")
        monkeypatch.setattr(h, "_find_media_by_uuid", lambda uid: p if uid == "xyz" else None)

        audio = h._extract_clipboard_audio("[audio:xyz]")
        assert audio == [b"OLDAUDIO"]


class TestTransformForHistory:
    def test_rewrites_image_marker_to_persistent_uuid(self):
        h = make_handler()
        h._clipboard_images[1] = ("uuid-1", "/media/uuid-1.png")

        result = h._transform_for_history("look at [image #1]")
        assert result == "look at [image:uuid-1]"

    def test_rewrites_audio_and_video_markers(self):
        h = make_handler()
        h._clipboard_audio[1] = ("uuid-a", "/media/uuid-a.mp3")
        h._clipboard_video[2] = ("uuid-v", "/media/uuid-v.mp4")

        result = h._transform_for_history("[audio #1] then [video #2]")
        assert result == "[audio:uuid-a] then [video:uuid-v]"

    def test_drops_marker_for_unknown_index(self):
        h = make_handler()
        result = h._transform_for_history("orphan [image #5] marker")
        assert result == "orphan  marker"

    def test_strips_paste_marker_entirely(self):
        h = make_handler()
        result = h._transform_for_history("before [paste #1 +10 lines] after")
        assert result == "before  after"

    def test_strips_whitespace_from_result(self):
        h = make_handler()
        result = h._transform_for_history("  [paste #1 +10 lines]  ")
        assert result == ""


class TestSanitizePaste:
    def test_normalizes_crlf_to_lf(self):
        h = make_handler()
        h._layout = MagicMock()
        h._layout.input.text = ""
        h._layout.input._cursor = 0
        assert h._sanitize_paste("a\r\nb\rc") == "a\nb\nc"

    def test_expands_tabs(self):
        h = make_handler()
        h._layout.input.text = ""
        h._layout.input._cursor = 0
        assert h._sanitize_paste("a\tb") == "a    b"

    def test_strips_trailing_newlines(self):
        h = make_handler()
        h._layout.input.text = ""
        h._layout.input._cursor = 0
        assert h._sanitize_paste("hello\n\n") == "hello"

    def test_drops_non_printable_characters(self):
        h = make_handler()
        h._layout.input.text = ""
        h._layout.input._cursor = 0
        assert h._sanitize_paste("a\x00\x01b") == "ab"

    def test_prepends_space_before_path_after_word_char(self):
        h = make_handler()
        h._layout.input.text = "open"
        h._layout.input._cursor = 4
        assert h._sanitize_paste("/tmp/file") == " /tmp/file"

    def test_no_space_prepended_at_start_of_input(self):
        h = make_handler()
        h._layout.input.text = ""
        h._layout.input._cursor = 0
        assert h._sanitize_paste("/tmp/file") == "/tmp/file"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
