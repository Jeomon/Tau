"""Tests for RPC multimodal attachments.

Covers tau/modes/rpc/mode.py:
  - _resolve_attachments() source routing (data/path/url) and validation
  - _handle_command "prompt" wiring media into runtime.invoke / with_media
"""

from __future__ import annotations

import base64

import pytest

import tau.modes.rpc.mode as mode
from tau.message.types import AudioContent, ImageContent, UserMessage

# ── _resolve_attachments ─────────────────────────────────────────────────────


class TestResolveAttachments:
    def test_none_and_empty_return_empty_buckets(self):
        assert mode._resolve_attachments(None) == ([], [], [], [])
        assert mode._resolve_attachments([]) == ([], [], [], [])

    def test_base64_data_kept_as_string_per_kind(self):
        atts = [
            {"kind": "image", "data": "aW1n"},
            {"kind": "audio", "data": "YXVk"},
            {"kind": "video", "data": "dmlk"},
            {"kind": "file", "data": "Zmls"},
        ]
        images, audio, video, file = mode._resolve_attachments(atts)
        assert images == ["aW1n"]
        assert audio == ["YXVk"]
        assert video == ["dmlk"]
        assert file == ["Zmls"]

    def test_url_allowed_for_images_only(self):
        images, *_ = mode._resolve_attachments([{"kind": "image", "url": "https://x/y.png"}])
        assert images == ["https://x/y.png"]

    def test_url_rejected_for_non_image(self):
        with pytest.raises(ValueError, match="only supported for images"):
            mode._resolve_attachments([{"kind": "audio", "url": "https://x/y.mp3"}])

    def test_path_is_read_into_bytes(self, tmp_path):
        p = tmp_path / "clip.bin"
        p.write_bytes(b"\x00\x01rawbytes")
        _, audio, _, _ = mode._resolve_attachments([{"kind": "audio", "path": str(p)}])
        assert audio == [b"\x00\x01rawbytes"]

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError, match="invalid or missing 'kind'"):
            mode._resolve_attachments([{"kind": "gif", "data": "x"}])

    def test_missing_source_raises(self):
        with pytest.raises(ValueError, match="exactly one of"):
            mode._resolve_attachments([{"kind": "image"}])

    def test_multiple_sources_raise(self):
        with pytest.raises(ValueError, match="exactly one of"):
            mode._resolve_attachments([{"kind": "image", "data": "a", "url": "https://x"}])

    def test_non_object_attachment_raises(self):
        with pytest.raises(ValueError, match="must be an object"):
            mode._resolve_attachments(["not-a-dict"])


# ── with_media integration (the resolved sources must produce real content) ──


def test_resolved_sources_build_multimodal_user_message():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    images, audio, video, file = mode._resolve_attachments(
        [
            {"kind": "image", "data": png},
            {"kind": "audio", "data": "YXVkaW8="},
        ]
    )
    msg = UserMessage.with_media("hi", images or None, audio or None, video or None, file or None)
    kinds = [type(c) for c in msg.contents]
    assert ImageContent in kinds
    assert AudioContent in kinds


# ── _handle_command "prompt" wiring ──────────────────────────────────────────


class _FakeRuntime:
    def __init__(self):
        self.agent = None  # not streaming → non-streaming invoke path
        self.invoked: list = []

    async def invoke(self, text, options=None):
        self.invoked.append((text, options))


@pytest.fixture
def captured(monkeypatch):
    lines: list = []
    monkeypatch.setattr(mode, "_write", lambda obj: lines.append(obj))
    return lines


@pytest.mark.asyncio
async def test_prompt_with_attachments_routes_media_into_prompt_options(captured):
    rt = _FakeRuntime()
    cmd = {
        "type": "prompt",
        "id": "1",
        "message": "look",
        "attachments": [
            {"kind": "image", "data": "aW1n"},
            {"kind": "file", "data": "Zmls"},
        ],
    }
    await mode._handle_command(cmd, rt, {})

    assert len(rt.invoked) == 1
    text, options = rt.invoked[0]
    assert text == "look"
    assert options is not None
    assert options.images == ["aW1n"]
    assert options.file == ["Zmls"]
    assert options.audio == [] and options.video == []
    assert captured and captured[-1]["success"] is True


@pytest.mark.asyncio
async def test_prompt_media_only_is_allowed(captured):
    rt = _FakeRuntime()
    cmd = {"type": "prompt", "id": "2", "attachments": [{"kind": "image", "data": "aW1n"}]}
    await mode._handle_command(cmd, rt, {})

    assert len(rt.invoked) == 1
    text, options = rt.invoked[0]
    assert text == ""
    assert options.images == ["aW1n"]


@pytest.mark.asyncio
async def test_prompt_text_only_takes_plain_invoke(captured):
    rt = _FakeRuntime()
    await mode._handle_command({"type": "prompt", "id": "3", "message": "hi"}, rt, {})
    text, options = rt.invoked[0]
    assert text == "hi"
    assert options is None  # no media → plain runtime.invoke(text)


@pytest.mark.asyncio
async def test_prompt_invalid_attachment_errors_without_invoking(captured):
    rt = _FakeRuntime()
    cmd = {"type": "prompt", "id": "4", "message": "x", "attachments": [{"kind": "audio", "url": "https://x"}]}
    await mode._handle_command(cmd, rt, {})

    assert rt.invoked == []
    assert captured[-1]["success"] is False
    assert "invalid attachment" in captured[-1]["error"]


@pytest.mark.asyncio
async def test_prompt_empty_message_and_no_attachments_errors(captured):
    rt = _FakeRuntime()
    await mode._handle_command({"type": "prompt", "id": "5"}, rt, {})
    assert rt.invoked == []
    assert captured[-1]["success"] is False
