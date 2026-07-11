"""Tests for tau/message/types.py — message type construction and methods."""

from __future__ import annotations

from tau.inference.types import StopReason
from tau.message.types import (
    AssistantMessage,
    AudioContent,
    FileContent,
    ImageContent,
    Role,
    SystemMessage,
    TerminalExecutionMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    Usage,
    UserMessage,
    VideoContent,
)


class TestTextContent:
    def test_default_type(self):
        c = TextContent(content="hello")
        assert c.type == "text"
        assert c.content == "hello"

    def test_empty_content(self):
        c = TextContent()
        assert c.content == ""


class TestThinkingContent:
    def test_fields(self):
        c = ThinkingContent(content="thought", signature="sig")
        assert c.type == "thinking"
        assert c.content == "thought"
        assert c.signature == "sig"


class TestToolCallContent:
    def test_fields(self):
        c = ToolCallContent(id="c1", name="my_tool", args={"x": 1})
        assert c.type == "tool_call"
        assert c.id == "c1"
        assert c.name == "my_tool"
        assert c.args == {"x": 1}


class TestToolResultContent:
    def test_defaults(self):
        c = ToolResultContent(id="c1", content="result")
        assert c.is_error is False
        assert c.terminate is False

    def test_error_flag(self):
        c = ToolResultContent(id="c1", content="error", is_error=True)
        assert c.is_error is True


class TestSystemMessage:
    def test_from_text(self):
        msg = SystemMessage.text("Be helpful.")
        assert msg.role == Role.SYSTEM
        assert len(msg.contents) == 1
        assert isinstance(msg.contents[0], TextContent)
        assert msg.contents[0].content == "Be helpful."

    def test_has_id_and_timestamp(self):
        msg = SystemMessage.text("x")
        assert msg.id
        assert msg.timestamp > 0


class TestUserMessage:
    def test_from_text(self):
        msg = UserMessage.from_text("Hello!")
        assert msg.role == Role.USER
        assert msg.contents[0].content == "Hello!"

    def test_with_images(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        msg = UserMessage.with_images("look at this", [png])
        assert len(msg.contents) == 2
        assert isinstance(msg.contents[1], ImageContent)

    def test_with_audio(self):
        audio = b"ID3" + b"\x00" * 20
        msg = UserMessage.with_audio("listen to this", [audio])
        assert len(msg.contents) == 2
        assert isinstance(msg.contents[1], AudioContent)

    def test_with_video(self):
        video = b"\x00\x00\x00\x18" + b"\x00" * 20
        msg = UserMessage.with_video("watch this", [video])
        assert len(msg.contents) == 2
        assert isinstance(msg.contents[1], VideoContent)

    def test_with_media_multiple_types(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        audio = b"ID3" + b"\x00" * 20
        msg = UserMessage.with_media("test", images=[png], audio=[audio])
        types = [type(c) for c in msg.contents]
        assert TextContent in types
        assert ImageContent in types
        assert AudioContent in types

    def test_empty_message(self):
        msg = UserMessage()
        assert msg.contents == []


class TestAssistantMessage:
    def test_from_text(self):
        msg = AssistantMessage.from_text("reply")
        assert msg.role == Role.ASSISTANT
        assert msg.text_content() == "reply"

    def test_text_content_concatenates(self):
        msg = AssistantMessage(
            contents=[  # type: ignore[arg-type]
                TextContent(content="foo"),
                TextContent(content="bar"),
            ]
        )
        assert msg.text_content() == "foobar"

    def test_tool_calls_extracted(self):
        tc = ToolCallContent(id="1", name="fn", args={})
        msg = AssistantMessage(contents=[TextContent(content="text"), tc])
        calls = msg.tool_calls()
        assert len(calls) == 1
        assert calls[0] is tc

    def test_tool_calls_empty_when_none(self):
        msg = AssistantMessage.from_text("plain")
        assert msg.tool_calls() == []

    def test_thinking_extracted(self):
        th = ThinkingContent(content="thought")
        msg = AssistantMessage(contents=[th, TextContent(content="text")])
        thinking = msg.thinking()
        assert len(thinking) == 1
        assert thinking[0] is th

    def test_thinking_empty_when_none(self):
        msg = AssistantMessage.from_text("plain")
        assert msg.thinking() == []

    def test_default_stop_reason(self):
        msg = AssistantMessage.from_text("x")
        assert msg.stop_reason == StopReason.Stop


class TestToolMessage:
    def test_from_result(self):
        r = ToolResultContent(id="c1", content="ok")
        msg = ToolMessage.from_result(r)
        assert msg.role == Role.TOOL
        assert len(msg.contents) == 1

    def test_from_results(self):
        r1 = ToolResultContent(id="c1", content="a")
        r2 = ToolResultContent(id="c2", content="b")
        msg = ToolMessage.from_results([r1, r2])
        assert len(msg.contents) == 2


class TestTerminalExecutionMessage:
    def test_to_user_message_with_output(self):
        msg = TerminalExecutionMessage(command="ls", output="file.txt")
        user_msg = msg.to_user_message()
        assert isinstance(user_msg, UserMessage)
        text = user_msg.contents[0].content
        assert "ls" in text
        assert "file.txt" in text

    def test_to_user_message_no_output(self):
        msg = TerminalExecutionMessage(command="ls", output="")
        user_msg = msg.to_user_message()
        text = user_msg.contents[0].content
        assert "no output" in text.lower()

    def test_to_user_message_cancelled(self):
        msg = TerminalExecutionMessage(command="ls", output="", cancelled=True)
        user_msg = msg.to_user_message()
        text = user_msg.contents[0].content
        assert "cancelled" in text.lower()

    def test_to_user_message_nonzero_exit(self):
        msg = TerminalExecutionMessage(command="false", output="", exit_code=1)
        user_msg = msg.to_user_message()
        text = user_msg.contents[0].content
        assert "1" in text


class TestImageContent:
    def test_url_passthrough(self):
        ic = ImageContent(images=["https://example.com/img.png"])
        pairs = ic.to_base64()
        assert pairs[0][0] == "https://example.com/img.png"

    def test_from_url(self):
        ic = ImageContent.from_url("https://example.com/img.png")
        assert ic.images[0] == "https://example.com/img.png"

    def test_bytes_normalized_to_base64(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        ic = ImageContent(images=[png])
        # After __post_init__, bytes should be stored as base64 string
        assert isinstance(ic.images[0], str)


class TestUsage:
    def test_defaults(self):
        u = Usage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cost.total == 0.0


class TestAudioContentMethods:
    def test_from_base64(self):
        from tau.message.types import AudioContent

        b64 = "SUQz"  # ID3 magic in base64 prefix
        ac = AudioContent.from_base64(b64)
        assert ac.audios == [b64]

    def test_from_base64_roundtrip(self):
        import base64

        from tau.message.types import AudioContent

        raw = b"ID3" + b"\x00" * 10
        b64 = base64.b64encode(raw).decode()
        ac = AudioContent.from_base64(b64)
        result_b64, mime = ac.to_base64()[0]
        assert mime == "audio/mpeg"
        assert result_b64 == b64

    def test_from_file(self, tmp_path):
        from tau.message.types import AudioContent

        f = tmp_path / "sound.mp3"
        f.write_bytes(b"ID3" + b"\x00" * 10)
        ac = AudioContent.from_file(f)
        assert len(ac.audios) == 1

    def test_to_base64_with_bytes(self):
        from tau.message.types import AudioContent

        raw = b"ID3" + b"\x00" * 10
        ac = AudioContent(audios=[raw])
        b64, mime = ac.to_base64()[0]
        assert mime == "audio/mpeg"
        import base64

        assert base64.b64decode(b64)[:3] == b"ID3"

    def test_post_init_normalizes_raw_bytes_to_base64_str(self):
        # Constructing with raw bytes must eagerly become a base64 str, not
        # stay as bytes — pydantic can't JSON-serialize raw bytes for session
        # persistence (mirrors ImageContent.__post_init__).
        from tau.message.types import AudioContent

        raw = b"ID3" + b"\x00" * 10
        ac = AudioContent(audios=[raw])
        assert isinstance(ac.audios[0], str)

    def test_post_init_leaves_existing_base64_str_untouched(self):
        from tau.message.types import AudioContent

        b64 = "SUQz"
        ac = AudioContent(audios=[b64])
        assert ac.audios == [b64]


class TestVideoContentMethods:
    def test_from_file(self, tmp_path):
        from tau.message.types import VideoContent

        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00\x00\x00\x18ftyp")
        vc = VideoContent.from_file(f)
        assert len(vc.videos) == 1

    def test_to_base64_with_bytes(self):
        from tau.message.types import VideoContent

        raw = b"\x00\x01\x02\x03"
        vc = VideoContent(videos=[raw])
        b64, mime = vc.to_base64()[0]
        assert mime == "video/mp4"
        import base64

        assert base64.b64decode(b64) == raw

    def test_post_init_normalizes_raw_bytes_to_base64_str(self):
        from tau.message.types import VideoContent

        vc = VideoContent(videos=[b"\x00\x01\x02\x03"])
        assert isinstance(vc.videos[0], str)


class TestFileContentMethods:
    def test_from_file(self, tmp_path):
        from tau.message.types import FileContent

        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        fc = FileContent.from_file(f)
        assert len(fc.files) == 1

    def test_to_base64_with_bytes(self):
        import base64

        from tau.message.types import FileContent

        raw = b"%PDF-1.4 fake"
        fc = FileContent(files=[raw])
        b64, mime = fc.to_base64()[0]
        assert mime == "application/pdf"
        assert base64.b64decode(b64) == raw

    def test_post_init_normalizes_raw_bytes_to_base64_str(self):
        from tau.message.types import FileContent

        fc = FileContent(files=[b"%PDF-1.4 fake"])
        assert isinstance(fc.files[0], str)

    def test_docx_mime_detected_from_base64_string_after_post_init(self):
        # Regression guard: __post_init__ eagerly base64-encodes on
        # construction, so to_base64() must decode the *full* stored string
        # (not a truncated prefix) to still tell docx/xlsx/pptx apart — a
        # truncated-prefix check can only ever see "some zip", never which one.
        import io
        import zipfile

        from tau.message.types import FileContent

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", "<fake/>")
        docx_bytes = buf.getvalue()

        fc = FileContent(files=[docx_bytes])
        _, mime = fc.to_base64()[0]
        assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class TestBranchSummaryMessage:
    def test_construction(self):
        from tau.message.types import BranchSummaryMessage, Role

        msg = BranchSummaryMessage(summary="branch done", from_id="entry123")
        assert msg.summary == "branch done"
        assert msg.from_id == "entry123"
        assert msg.role == Role.BRANCH_SUMMARY

    def test_defaults(self):
        from tau.message.types import BranchSummaryMessage

        msg = BranchSummaryMessage()
        assert msg.summary == ""
        assert msg.from_id == ""
        assert isinstance(msg.timestamp, float)


class TestCompactionSummaryMessageDirect:
    def test_construction(self):
        from tau.message.types import CompactionSummaryMessage, Role

        msg = CompactionSummaryMessage(summary="ctx summary", tokens_before=5000)
        assert msg.summary == "ctx summary"
        assert msg.tokens_before == 5000
        assert msg.role == Role.COMPACTION_SUMMARY

    def test_defaults(self):
        from tau.message.types import CompactionSummaryMessage

        msg = CompactionSummaryMessage()
        assert msg.summary == ""
        assert msg.tokens_before == 0


class TestMediaContentSessionPersistenceRoundTrip:
    """Regression coverage for a real bug: AudioContent/VideoContent/FileContent
    used to crash (or silently corrupt) on session save/reload.

    Two distinct causes, both now fixed:
    1. No __post_init__ normalization meant raw (non-UTF-8) bytes reached
       pydantic's JSON encoder directly, which cannot serialize them at all —
       PydanticSerializationError on the very first save.
    2. Even after adding normalization, the field was typed `list[bytes | str]`
       (bytes listed first) — pydantic's union validator coerced a reloaded
       JSON string back into bytes, re-triggering the bytes branch and
       double-base64-encoding the content on every reload. Fixed by reordering
       to `list[str | bytes]`, matching ImageContent's existing field order.
    """

    # Genuinely non-UTF-8 bytes — the exact shape that used to crash serialization.
    _BINARY = bytes([0x25, 0x50, 0x44, 0x46, 0xFF, 0xFE, 0x00, 0x80, 0x81, 0x82])

    def _round_trip(self, content):
        import base64

        from tau.session.types import MessageEntry

        msg = UserMessage(contents=[content])
        entry = MessageEntry(message=msg, parent_id=None)
        json_str = entry.model_dump_json(exclude_none=True)  # must not raise
        reloaded = MessageEntry.model_validate_json(json_str)
        b64, _ = reloaded.message.contents[0].to_base64()[0]
        return base64.b64decode(b64), reloaded

    def test_audio_content_survives_persistence_with_binary_data(self):
        recovered, _ = self._round_trip(AudioContent(audios=[self._BINARY]))
        assert recovered == self._BINARY

    def test_video_content_survives_persistence_with_binary_data(self):
        recovered, _ = self._round_trip(VideoContent(videos=[self._BINARY]))
        assert recovered == self._BINARY

    def test_file_content_survives_persistence_with_binary_data(self):
        recovered, _ = self._round_trip(FileContent(files=[self._BINARY]))
        assert recovered == self._BINARY

    def test_image_content_still_survives_persistence(self):
        # Baseline: this one already worked before the fix — guards against
        # a future edit breaking the field that was always correct.
        recovered, _ = self._round_trip(ImageContent(images=[self._BINARY]))
        assert recovered == self._BINARY

    def test_survives_a_second_persistence_cycle(self):
        # The double-encoding bug only manifested on a *second* round trip
        # (reload -> re-save -> reload), since the first reload was where the
        # type flipped from str to bytes.
        import base64

        from tau.session.types import MessageEntry

        _, reloaded = self._round_trip(FileContent(files=[self._BINARY]))
        json_str_2 = reloaded.model_dump_json(exclude_none=True)
        reloaded_2 = MessageEntry.model_validate_json(json_str_2)
        b64, _ = reloaded_2.message.contents[0].to_base64()[0]
        assert base64.b64decode(b64) == self._BINARY

    def test_reloaded_field_stays_str_not_bytes(self):
        # Direct assertion on the union-order fix: after one reload, the
        # stored item must still be `str`, never coerced back to `bytes`.
        from tau.session.types import MessageEntry

        msg = UserMessage(contents=[FileContent(files=[self._BINARY])])
        entry = MessageEntry(message=msg, parent_id=None)
        reloaded = MessageEntry.model_validate_json(entry.model_dump_json(exclude_none=True))
        assert isinstance(reloaded.message.contents[0].files[0], str)
