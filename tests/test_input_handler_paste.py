"""Tests for tau/modes/interactive/input_handler.py — paste marker round-trips.

Covers the [paste #N], [image/audio/video/file #N], and persistent [type:uuid]
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
    h._clipboard_files = {}
    h._clipboard_file_counter = 0
    h._layout = MagicMock()
    h._layout.input.text = ""
    h._layout.input._cursor = 0
    h._tui = MagicMock()
    # Only touched by _paste_file/_store_clipboard_*, which route media dir
    # writes through fully-mocked attributes — no real disk I/O occurs unless
    # a test explicitly points session_dir at a real tmp_path.
    h._runtime = MagicMock()
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


class TestFilePasteMarker:
    def test_extract_clipboard_file_reads_bytes(self, tmp_path):
        h = make_handler()
        p = tmp_path / "report.pdf"
        p.write_bytes(b"%PDF-DATA")
        h._clipboard_files[1] = ("uuid-f", str(p))

        file = h._extract_clipboard_file("[file #1]")
        assert file == [b"%PDF-DATA"]
        assert h._clipboard_files == {}
        assert h._clipboard_file_counter == 0

    def test_extract_deduplicates_repeated_marker(self, tmp_path):
        h = make_handler()
        p = tmp_path / "report.pdf"
        p.write_bytes(b"%PDF-DATA")
        h._clipboard_files[1] = ("uuid-f", str(p))

        file = h._extract_clipboard_file("[file #1] and again [file #1]")
        assert file == [b"%PDF-DATA"]

    def test_extract_skips_unknown_index(self):
        h = make_handler()
        assert h._extract_clipboard_file("[file #99]") == []

    def test_extract_file_persistent_uuid_marker(self, tmp_path, monkeypatch):
        h = make_handler()
        p = tmp_path / "old.pdf"
        p.write_bytes(b"OLDPDF")
        monkeypatch.setattr(h, "_find_media_by_uuid", lambda uid: p if uid == "abc" else None)

        file = h._extract_clipboard_file("[file:abc]")
        assert file == [b"OLDPDF"]

    def test_extract_persistent_uuid_marker_missing_is_skipped(self, monkeypatch):
        h = make_handler()
        monkeypatch.setattr(h, "_find_media_by_uuid", lambda _uid: None)

        assert h._extract_clipboard_file("[file:gone]") == []


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

    def test_rewrites_file_marker_to_persistent_uuid(self):
        h = make_handler()
        h._clipboard_files[1] = ("uuid-f", "/media/uuid-f.pdf")

        result = h._transform_for_history("report: [file #1]")
        assert result == "report: [file:uuid-f]"

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


class TestStripMediaMarkers:
    def test_strips_all_four_marker_kinds(self):
        h = make_handler()
        text = "see [image #1] and [audio:uuid1] and [video #2] and [file:uuid2]"
        stripped = h._strip_media_markers(text)
        assert "[image" not in stripped
        assert "[audio" not in stripped
        assert "[video" not in stripped
        assert "[file" not in stripped

    def test_leaves_plain_text_untouched(self):
        h = make_handler()
        assert h._strip_media_markers("just plain text") == "just plain text"

    def test_falls_back_to_original_when_stripping_leaves_nothing(self):
        h = make_handler()
        assert h._strip_media_markers("[file #1]") == "[file #1]"

    def test_preserves_surrounding_words(self):
        h = make_handler()
        assert h._strip_media_markers("before [file #1] after") == "before  after"


class TestDetectPastedFilePath:
    """Covers the macOS clipboard-file gap: Pillow's ImageGrab.grabclipboard()
    can only ever return an Image or None on macOS (no file-list support,
    unlike Windows), so Ctrl+V of a Finder-copied file always returns None.
    The *only* signal Tau actually receives for a copied/dragged file on
    macOS is a plain-text bracketed paste of its path — so _on_paste_text
    must detect that shape and route it through _paste_file instead of
    inserting it as literal text.
    """

    def test_bare_existing_path_is_detected(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"ID3fake")
        assert h._detect_pasted_file_path(str(p)) == str(p)

    def test_tilde_path_is_expanded_and_detected(self, tmp_path, monkeypatch):
        h = make_handler()
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.expanduser() uses this on Windows
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"ID3fake")
        assert h._detect_pasted_file_path("~/clip.mp3") == str(p)

    def test_nonexistent_path_returns_none(self):
        h = make_handler()
        assert h._detect_pasted_file_path("/definitely/not/a/real/path.mp3") is None

    def test_relative_looking_text_returns_none(self):
        h = make_handler()
        assert h._detect_pasted_file_path("some/relative/thing") is None

    def test_plain_prose_returns_none(self):
        h = make_handler()
        assert h._detect_pasted_file_path("just some pasted text") is None

    def test_sentence_containing_a_path_returns_none(self):
        # Only a *bare* whole-paste path counts — a path mentioned inside
        # ordinary text must still be treated as literal text.
        h = make_handler()
        assert h._detect_pasted_file_path("check /etc/hosts please") is None

    def test_multiline_text_returns_none(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"ID3fake")
        assert h._detect_pasted_file_path(f"{p}\nsecond line") is None

    def test_directory_path_returns_none(self, tmp_path):
        h = make_handler()
        assert h._detect_pasted_file_path(str(tmp_path)) is None

    def test_on_paste_text_routes_bare_file_path_through_paste_file(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"ID3fake")

        h._on_paste_text(str(p))

        marker = h._layout.input.insert_at_cursor.call_args[0][0]
        assert marker == "[audio #1]"
        assert h._clipboard_audio

    def test_on_paste_text_still_inserts_plain_text_normally(self):
        h = make_handler()
        h._on_paste_text("just some pasted text")
        h._layout.input.insert_at_cursor.assert_called_once_with("just some pasted text")


class TestPasteFileRouting:
    """Covers the _paste_file bug fix: a non-audio/video/image file (PDF,
    DOCX, ...) used to fall through to the image store and fail with a
    misleading "could not store image" error. It must now route to the
    dedicated file store, while real images/audio/video keep working.
    """

    def test_pdf_routes_to_file_store_not_image(self, tmp_path):
        h = make_handler()
        p = tmp_path / "report.pdf"
        p.write_bytes(b"%PDF-1.4 fake pdf")

        h._paste_file(str(p))

        assert h._clipboard_files, "PDF should be stored as a file"
        assert not h._clipboard_images, "PDF must not be stored as an image"
        marker = h._layout.input.insert_at_cursor.call_args[0][0]
        assert marker.startswith("[file #")

    def test_docx_routes_to_file_store(self, tmp_path):
        h = make_handler()
        p = tmp_path / "notes.docx"
        p.write_bytes(b"PK\x03\x04fakedocx")

        h._paste_file(str(p))

        assert h._clipboard_files
        assert not h._clipboard_images

    def test_unknown_extension_routes_to_file_store(self, tmp_path):
        h = make_handler()
        p = tmp_path / "data.xyz123"
        p.write_bytes(b"whatever")

        h._paste_file(str(p))

        assert h._clipboard_files
        assert not h._clipboard_images

    def test_known_image_suffix_routes_to_image_store(self, tmp_path, monkeypatch):
        h = make_handler()
        # Bypass real PIL decoding — only the routing decision is under test.
        monkeypatch.setattr(
            "tau.utils.image_processing.process_image",
            lambda raw, auto_resize=True: type(
                "R",
                (),
                {"data": raw, "mime_type": "image/png", "dimension_note": lambda self: None},
            )(),
        )
        p = tmp_path / "photo.png"
        p.write_bytes(b"fakepngbytes")

        h._paste_file(str(p))

        assert h._clipboard_images, "known image suffix should route to the image store"
        assert not h._clipboard_files

    def test_audio_suffix_still_routes_to_audio_store(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"ID3fakeaudio")

        h._paste_file(str(p))

        assert h._clipboard_audio
        assert not h._clipboard_files

    def test_video_suffix_still_routes_to_video_store(self, tmp_path):
        h = make_handler()
        p = tmp_path / "clip.mp4"
        p.write_bytes(b"fakevideobytes")

        h._paste_file(str(p))

        assert h._clipboard_video
        assert not h._clipboard_files

    def test_extensionless_path_routes_to_image_store(self, tmp_path, monkeypatch):
        # Preserves the pre-fix fallback for extensionless clipboard grabs
        # (e.g. a screenshot saved without a suffix).
        monkeypatch.setattr(
            "tau.utils.image_processing.process_image",
            lambda raw, auto_resize=True: type(
                "R",
                (),
                {"data": raw, "mime_type": "image/png", "dimension_note": lambda self: None},
            )(),
        )
        h = make_handler()
        p = tmp_path / "noext"
        p.write_bytes(b"fakepngbytes")

        h._paste_file(str(p))

        assert h._clipboard_images
        assert not h._clipboard_files


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


class TestCommandPasteExpansion:
    """Slash/terminal input must dispatch with pastes EXPANDED.

    Regression: `/darwin <5KB pasted brief>` used to reach the command with the
    literal "[paste #N ...]" placeholder while the buffer was cleared — the
    content was destroyed before anything could read it.
    """

    def _submit_ready_handler(self, monkeypatch):
        h = make_handler()
        h.save_history = MagicMock()
        h._make_slash_message = MagicMock(return_value="slash-msg")
        h._track_task = MagicMock()
        h._invoke = MagicMock(return_value=None)
        h._deferred_inputs = []
        h._runtime.agent = None
        import asyncio as _asyncio

        monkeypatch.setattr(_asyncio, "ensure_future", lambda x: x)
        return h

    def test_slash_command_dispatches_expanded_paste(self, monkeypatch):
        h = self._submit_ready_handler(monkeypatch)
        body = "GOAL: " + "x" * 3000
        h._on_paste_text(body)
        marker = h._layout.input.insert_at_cursor.call_args[0][0]
        assert marker.startswith("[paste #1")

        h._on_submit(f"/darwin {marker}")

        dispatched = h._invoke.call_args[0][0]
        assert dispatched == f"/darwin {body}"          # content, not placeholder
        assert h._pasted_texts == {}                     # buffers consumed
        # transcript shows the compact original, not 3KB of paste
        assert h._make_slash_message.call_args[0][0] == f"/darwin {marker}"

    def test_terminal_command_dispatches_expanded_paste(self, monkeypatch):
        h = self._submit_ready_handler(monkeypatch)
        body = "\n".join(f"line {i}" for i in range(60))
        h._on_paste_text(body)
        marker = h._layout.input.insert_at_cursor.call_args[0][0]

        h._on_submit(f"!cat <<'EOF'\n{marker}")
        dispatched = h._invoke.call_args[0][0]
        assert body in dispatched and "[paste #" not in dispatched

    def test_deferred_command_stores_expanded_text(self, monkeypatch):
        h = self._submit_ready_handler(monkeypatch)
        agent = MagicMock()
        agent.is_idle.return_value = False
        h._runtime.agent = agent
        h._runtime.commands.get.return_value = MagicMock(requires_idle=True)

        body = "y" * 3000
        h._on_paste_text(body)
        marker = h._layout.input.insert_at_cursor.call_args[0][0]

        h._on_submit(f"/darwin {marker}")
        assert h._deferred_inputs == [f"/darwin {body}"]  # replay after settle
        h._invoke.assert_not_called()                     # cannot re-expand later:
        assert h._pasted_texts == {}                      # buffers already consumed
