"""Tests for AudioContent wire-format conversion.

Gemini (generate/Vertex/Antigravity) and openai_user_content (shared by the
"openai_completions" provider family — reachable in practice only via
OpenRouter's audio-capable proxied models, see test_model_audio_modality.py)
both support it. Anthropic's API has no audio input support at all, and the
OpenAI Codex/Responses models Tau uses explicitly list audio as unsupported
(gpt-audio/gpt-audio-mini are a separate product line) — neither gets an
AudioContent conversion branch.
"""

from __future__ import annotations

import base64

from tau.message.types import AudioContent, TextContent, UserMessage

_AUDIO_BYTES = b"ID3" + b"\x00" * 20  # MP3 magic bytes
_AUDIO_BYTES_WAV = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20


class TestGeminiGenerateAudioContent:
    def _convert(self, msg):
        from tau.inference.api.text.gemini_generate import _messages_to_gemini

        _, contents = _messages_to_gemini([msg])
        return contents

    def test_audio_produces_inline_data_part(self):
        msg = UserMessage.with_media("here is a clip", audio=[_AUDIO_BYTES])
        contents = self._convert(msg)

        parts = contents[0].parts
        assert parts[0].text == "here is a clip"
        assert parts[1].inline_data.mime_type == "audio/mpeg"
        assert parts[1].inline_data.data == _AUDIO_BYTES

    def test_text_only_message_is_unaffected(self):
        msg = UserMessage.from_text("hello")
        contents = self._convert(msg)
        assert len(contents[0].parts) == 1
        assert contents[0].parts[0].text == "hello"


class TestGoogleVertexAudioContent:
    def _convert(self, msg):
        from tau.inference.api.text.google_vertex import _messages_to_gemini

        _, contents = _messages_to_gemini([msg])
        return contents

    def test_audio_produces_inline_data_part(self):
        msg = UserMessage.with_media("here is a clip", audio=[_AUDIO_BYTES])
        contents = self._convert(msg)

        parts = contents[0].parts
        assert parts[1].inline_data.mime_type == "audio/mpeg"
        assert parts[1].inline_data.data == _AUDIO_BYTES


class TestGoogleAntigravityAudioContent:
    def _convert(self, msg):
        from tau.inference.api.text.google_antigravity import _messages_to_contents

        _, contents = _messages_to_contents([msg])
        return contents

    def test_audio_produces_inline_data_dict(self):
        msg = UserMessage.with_media("here is a clip", audio=[_AUDIO_BYTES])
        contents = self._convert(msg)

        assert contents == [
            {
                "role": "user",
                "parts": [
                    {"text": "here is a clip"},
                    {
                        "inlineData": {
                            "mimeType": "audio/mpeg",
                            "data": base64.b64encode(_AUDIO_BYTES).decode(),
                        }
                    },
                ],
            }
        ]

    def test_text_only_message_is_unaffected(self):
        msg = UserMessage.from_text("hello")
        contents = self._convert(msg)
        assert contents == [{"role": "user", "parts": [{"text": "hello"}]}]


class TestOpenAIUserContentAudioContent:
    """openai_user_content is shared by every "openai_completions"-family
    provider (OpenAI Completions, GitHub Copilot, OpenAI Vertex, OpenRouter)
    plus Mistral. AudioContent is only reachable here for models explicitly
    flagged with Modality.Audio — currently a subset of OpenRouter's proxied
    models, none of Mistral's or the others'.
    """

    def test_mp3_produces_input_audio_part_with_mp3_format(self):
        from tau.inference.api.text.utils import openai_user_content

        parts = openai_user_content(
            [TextContent(content="here is a clip"), AudioContent(audios=[_AUDIO_BYTES])]
        )
        assert parts == [
            {"type": "text", "text": "here is a clip"},
            {
                "type": "input_audio",
                "input_audio": {
                    "data": base64.b64encode(_AUDIO_BYTES).decode(),
                    "format": "mp3",
                },
            },
        ]

    def test_wav_produces_input_audio_part_with_wav_format(self):
        from tau.inference.api.text.utils import openai_user_content

        parts = openai_user_content([AudioContent(audios=[_AUDIO_BYTES_WAV])])
        assert parts[0]["input_audio"]["format"] == "wav"

    def test_text_only_message_is_unaffected(self):
        from tau.inference.api.text.utils import openai_user_content

        result = openai_user_content([TextContent(content="hello")])
        assert result == "hello"


class TestAudioContentNotWiredElsewhere:
    """Confirms Anthropic and OpenAI Codex Responses still ignore AudioContent —
    they have no case AudioContent() branch, so it should be silently dropped,
    not raise, and not appear in the wire payload.
    """

    def test_anthropic_drops_audio_content_silently(self):
        from tau.inference.api.text.utils import anthropic_messages_to_list

        msg = UserMessage.with_media("here is a clip", audio=[_AUDIO_BYTES])
        _, result = anthropic_messages_to_list([msg])
        parts = result[0]["content"]
        assert parts == [{"type": "text", "text": "here is a clip"}]

    def test_openai_codex_responses_drops_audio_content_silently(self):
        from tau.inference.api.text.openai_codex_responses import _messages_to_input

        msg = UserMessage.with_media("here is a clip", audio=[_AUDIO_BYTES])
        _, items = _messages_to_input([msg])
        assert items == [
            {"role": "user", "content": [{"type": "input_text", "text": "here is a clip"}]}
        ]
