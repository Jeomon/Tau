"""Tests for FileContent wire-format conversion across the providers with native
document support: Anthropic (+ shared anthropic_messages_to_list), Gemini
(generate/Vertex/Antigravity), and OpenAI Codex Responses.

Each provider gets: a FileContent-only message produces the correct native
block shape, a FileContent+TextContent message keeps both, and a plain
TextContent-only message is completely unaffected (no regression).
"""

from __future__ import annotations

import base64

from tau.message.types import FileContent, UserMessage

_PDF_BYTES = b"%PDF-1.4 fake pdf content"


class TestAnthropicFileContent:
    def _convert(self, msg):
        from tau.inference.api.text.utils import anthropic_messages_to_list

        _, result = anthropic_messages_to_list([msg])
        return result

    def test_file_with_text_produces_document_block(self):
        msg = UserMessage.with_media("here is my report", file=[_PDF_BYTES])
        result = self._convert(msg)

        assert result == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "here is my report"},
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.b64encode(_PDF_BYTES).decode(),
                        },
                    },
                ],
            }
        ]

    def test_file_only_gets_placeholder_text_not_image_label(self):
        # A genuinely text-less message (with_media always includes a
        # TextContent block, even for "", so has_text would be True there).
        msg = UserMessage(contents=[FileContent(files=[_PDF_BYTES])])
        result = self._convert(msg)
        parts = result[0]["content"]
        placeholder = next(p for p in parts if p["type"] == "text")
        assert placeholder["text"] == "(see attached file)"

    def test_text_only_message_is_unaffected(self):
        msg = UserMessage.from_text("just plain text")
        result = self._convert(msg)
        assert result == [
            {"role": "user", "content": [{"type": "text", "text": "just plain text"}]}
        ]


class TestGeminiGenerateFileContent:
    def _convert(self, msg):
        from tau.inference.api.text.gemini_generate import _messages_to_gemini

        _, contents = _messages_to_gemini([msg])
        return contents

    def test_file_produces_inline_data_part(self):
        msg = UserMessage.with_media("report attached", file=[_PDF_BYTES])
        contents = self._convert(msg)

        assert len(contents) == 1
        parts = contents[0].parts
        assert parts[0].text == "report attached"
        assert parts[1].inline_data.mime_type == "application/pdf"
        assert parts[1].inline_data.data == _PDF_BYTES

    def test_text_only_message_is_unaffected(self):
        msg = UserMessage.from_text("hello")
        contents = self._convert(msg)
        assert len(contents[0].parts) == 1
        assert contents[0].parts[0].text == "hello"


class TestGoogleVertexFileContent:
    def _convert(self, msg):
        from tau.inference.api.text.google_vertex import _messages_to_gemini

        _, contents = _messages_to_gemini([msg])
        return contents

    def test_file_produces_inline_data_part(self):
        msg = UserMessage.with_media("report attached", file=[_PDF_BYTES])
        contents = self._convert(msg)

        parts = contents[0].parts
        assert parts[1].inline_data.mime_type == "application/pdf"
        assert parts[1].inline_data.data == _PDF_BYTES


class TestGoogleAntigravityFileContent:
    def _convert(self, msg):
        from tau.inference.api.text.google_antigravity import _messages_to_contents

        _, contents = _messages_to_contents([msg])
        return contents

    def test_file_produces_inline_data_dict(self):
        msg = UserMessage.with_media("report attached", file=[_PDF_BYTES])
        contents = self._convert(msg)

        assert contents == [
            {
                "role": "user",
                "parts": [
                    {"text": "report attached"},
                    {
                        "inlineData": {
                            "mimeType": "application/pdf",
                            "data": base64.b64encode(_PDF_BYTES).decode(),
                        }
                    },
                ],
            }
        ]

    def test_text_only_message_is_unaffected(self):
        msg = UserMessage.from_text("hello")
        contents = self._convert(msg)
        assert contents == [{"role": "user", "parts": [{"text": "hello"}]}]


class TestOpenAICodexResponsesFileContent:
    def _convert(self, msg):
        from tau.inference.api.text.openai_codex_responses import _messages_to_input

        _, items = _messages_to_input([msg])
        return items

    def test_file_produces_input_file_item(self):
        msg = UserMessage.with_media("report attached", file=[_PDF_BYTES])
        items = self._convert(msg)

        b64 = base64.b64encode(_PDF_BYTES).decode()
        assert items == [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "report attached"},
                    {"type": "input_file", "file_data": f"data:application/pdf;base64,{b64}"},
                ],
            }
        ]

    def test_text_only_message_is_unaffected(self):
        msg = UserMessage.from_text("hello")
        items = self._convert(msg)
        assert items == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]


class TestFileContentMimeDetection:
    """Sanity check that non-PDF documents also round-trip through a provider
    with their real MIME type, not a hardcoded PDF assumption.
    """

    def test_docx_gets_correct_mime_type_end_to_end(self):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", "<fake/>")
        docx_bytes = buf.getvalue()

        from tau.inference.api.text.utils import anthropic_messages_to_list

        msg = UserMessage.with_media("resume", file=[docx_bytes])
        _, result = anthropic_messages_to_list([msg])
        doc_block = next(p for p in result[0]["content"] if p["type"] == "document")
        assert (
            doc_block["source"]["media_type"]
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
