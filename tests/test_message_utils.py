"""Tests for tau/message/utils.py — MIME detection, encoding, and message filtering."""

from __future__ import annotations

import base64

from tau.message.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    UserMessage,
)
from tau.message.utils import (
    audio_to_base64,
    detect_audio_mime,
    detect_image_mime,
    filter_empty_assistant_messages,
    image_to_base64,
    strip_unusable_trailing_assistant,
)

# --- JPEG magic bytes: FF D8 FF ---
JPEG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 20
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
GIF87_MAGIC = b"GIF87a" + b"\x00" * 20
GIF89_MAGIC = b"GIF89a" + b"\x00" * 20
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20

MP3_ID3 = b"ID3" + b"\x00" * 20
MP3_FF_FB = b"\xff\xfb" + b"\x00" * 20
OGG_MAGIC = b"OggS" + b"\x00" * 20
FLAC_MAGIC = b"fLaC" + b"\x00" * 20
WAV_MAGIC = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20


class TestDetectImageMime:
    def test_jpeg(self):
        assert detect_image_mime(JPEG_MAGIC) == "image/jpeg"

    def test_png(self):
        assert detect_image_mime(PNG_MAGIC) == "image/png"

    def test_gif87(self):
        assert detect_image_mime(GIF87_MAGIC) == "image/gif"

    def test_gif89(self):
        assert detect_image_mime(GIF89_MAGIC) == "image/gif"

    def test_webp(self):
        assert detect_image_mime(WEBP_MAGIC) == "image/webp"

    def test_unknown_defaults_to_png(self):
        assert detect_image_mime(b"\x00\x01\x02\x03") == "image/png"


class TestDetectAudioMime:
    def test_mp3_id3(self):
        assert detect_audio_mime(MP3_ID3) == "audio/mpeg"

    def test_mp3_ff_fb(self):
        assert detect_audio_mime(MP3_FF_FB) == "audio/mpeg"

    def test_ogg(self):
        assert detect_audio_mime(OGG_MAGIC) == "audio/ogg"

    def test_flac(self):
        assert detect_audio_mime(FLAC_MAGIC) == "audio/flac"

    def test_wav(self):
        assert detect_audio_mime(WAV_MAGIC) == "audio/wav"

    def test_unknown_defaults_to_mpeg(self):
        assert detect_audio_mime(b"\x00\x01\x02\x03") == "audio/mpeg"


class TestImageToBase64:
    def test_url_passthrough(self):
        url = "https://example.com/image.png"
        data, mime = image_to_base64(url)
        assert data == url
        assert mime == ""

    def test_bytes_jpeg(self):
        data, mime = image_to_base64(JPEG_MAGIC)
        assert mime == "image/jpeg"
        assert base64.b64decode(data)[:3] == b"\xff\xd8\xff"

    def test_bytes_png(self):
        data, mime = image_to_base64(PNG_MAGIC)
        assert mime == "image/png"

    def test_base64_string_detected_mime(self):
        b64 = base64.b64encode(PNG_MAGIC).decode()
        data, mime = image_to_base64(b64)
        assert data == b64
        assert mime == "image/png"


class TestAudioToBase64:
    def test_bytes_mp3(self):
        data, mime = audio_to_base64(MP3_ID3)
        assert mime == "audio/mpeg"
        assert base64.b64decode(data)[:3] == b"ID3"

    def test_bytes_wav(self):
        data, mime = audio_to_base64(WAV_MAGIC)
        assert mime == "audio/wav"

    def test_base64_string_passthrough(self):
        b64 = base64.b64encode(OGG_MAGIC).decode()
        data, mime = audio_to_base64(b64)
        assert data == b64
        assert mime == "audio/ogg"

    def test_file_path(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(MP3_ID3)
        data, mime = audio_to_base64(f"file:{audio_file}")
        assert mime == "audio/mpeg"
        assert base64.b64decode(data)[:3] == b"ID3"


class TestFilterEmptyAssistantMessages:
    def _make_assistant(self, *contents):
        msg = AssistantMessage()
        msg.contents = list(contents)
        return msg

    def test_keeps_assistant_with_text(self):
        msg = self._make_assistant(TextContent(content="hello"))
        result = filter_empty_assistant_messages([msg])
        assert result == [msg]

    def test_removes_empty_assistant(self):
        msg = self._make_assistant()
        result = filter_empty_assistant_messages([msg])
        assert result == []

    def test_keeps_assistant_with_tool_call(self):
        msg = self._make_assistant(ToolCallContent(id="1", name="fn", args={}))
        result = filter_empty_assistant_messages([msg])
        assert result == [msg]

    def test_keeps_assistant_with_thinking(self):
        msg = self._make_assistant(ThinkingContent(content="thought"))
        result = filter_empty_assistant_messages([msg])
        assert result == [msg]

    def test_keeps_non_assistant_messages(self):
        user = UserMessage.from_text("hi")
        empty_asst = self._make_assistant()
        result = filter_empty_assistant_messages([user, empty_asst])
        assert result == [user]

    def test_empty_list(self):
        assert filter_empty_assistant_messages([]) == []


class TestStripUnusableTrailingAssistant:
    def test_strips_trailing_assistant_with_tool_calls(self):
        user = UserMessage.from_text("hi")
        asst = AssistantMessage(contents=[ToolCallContent(id="1", name="fn", args={})])
        result = strip_unusable_trailing_assistant([user, asst])
        assert len(result) == 1
        assert result[0] is user

    def test_keeps_assistant_without_tool_calls(self):
        user = UserMessage.from_text("hi")
        asst = AssistantMessage(contents=[TextContent(content="reply")])
        result = strip_unusable_trailing_assistant([user, asst])
        assert len(result) == 2

    def test_empty_list(self):
        assert strip_unusable_trailing_assistant([]) == []

    def test_only_non_assistant_messages_unchanged(self):
        msgs = [UserMessage.from_text("a"), UserMessage.from_text("b")]
        result = strip_unusable_trailing_assistant(msgs)
        assert result == msgs


class TestVideoToBase64:
    def test_bytes_returns_base64_string(self):
        from tau.message.utils import video_to_base64

        data = b"\x00\x01\x02\x03"
        b64, mime = video_to_base64(data)
        import base64

        assert base64.b64decode(b64) == data
        assert mime == "video/mp4"

    def test_base64_string_passthrough(self):
        import base64

        from tau.message.utils import video_to_base64

        b64 = base64.b64encode(b"fake video").decode()
        result, mime = video_to_base64(b64)
        assert result == b64
        assert mime == "video/mp4"

    def test_file_path_reads_file(self, tmp_path):
        from tau.message.utils import video_to_base64

        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake video bytes")
        b64, mime = video_to_base64(f"file:{f}")
        import base64

        assert base64.b64decode(b64) == b"fake video bytes"
        assert mime == "video/mp4"
