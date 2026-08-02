"""Tool-produced images must be bounded before they enter the context window.

Providers validate every image in a request, not just the newest: Anthropic
drops its per-image cap from 8000px to 2000px once a request carries many
images, so an oversized screenshot accepted early in a session starts failing
*every* later request. It is written to the transcript, so it survives reload
and wedges the session permanently. `read`'s byte cap is no defence — a 3 MB
PNG can still be 8000px wide.

Only the interactive paste path used to call `process_image`; `read`, browser
and desktop screenshots, and any extension tool went through unprocessed.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from tau.agent.service import Agent
from tau.message.types import ImageContent
from tau.tool.types import ToolResult


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _dimensions(b64: str) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(b64))).size


class _FakeSettings:
    def __init__(self, auto_resize: bool = True) -> None:
        self._auto_resize = auto_resize

    def get_image_auto_resize(self) -> bool:
        return self._auto_resize


def _agent(auto_resize: bool = True, settings: object | None = _FakeSettings) -> Agent:
    """A bare Agent — only `_engine._settings` is touched by the code under test."""
    agent = Agent.__new__(Agent)
    resolved = _FakeSettings(auto_resize) if settings is _FakeSettings else settings
    agent._engine = type("_E", (), {"_settings": resolved})()
    return agent


def _result_with(images, content: str = "captured") -> ToolResult:
    return ToolResult(id="c1", content=content, image=ImageContent(images=images))


class TestOversizedImagesAreBounded:
    def test_oversized_screenshot_is_resized(self):
        result = _result_with([_png(2400, 4800)])
        _agent()._bound_result_images(result)
        width, height = _dimensions(result.image.images[0])
        assert max(width, height) <= 2000
        assert (width, height) == (1000, 2000)  # aspect ratio preserved

    def test_image_within_limits_is_left_alone(self):
        original = _b64(_png(800, 600))
        result = _result_with([original])
        _agent()._bound_result_images(result)
        assert _dimensions(result.image.images[0]) == (800, 600)

    def test_every_image_in_the_result_is_bounded(self):
        result = _result_with([_png(3000, 3000), _png(2500, 2500)])
        _agent()._bound_result_images(result)
        for encoded in result.image.images:
            assert max(_dimensions(encoded)) <= 2000

    def test_dimension_note_records_the_mapping(self):
        """The model needs this to map coordinates back onto the original."""
        result = _result_with([_png(2400, 4800)])
        _agent()._bound_result_images(result)
        assert "2400x4800" in result.image.dimension_note
        assert "Multiply coordinates" in result.image.dimension_note

    def test_no_note_when_nothing_was_resized(self):
        result = _result_with([_png(100, 100)])
        _agent()._bound_result_images(result)
        assert result.image.dimension_note is None

    def test_a_note_the_tool_set_itself_is_kept(self):
        result = ToolResult(
            id="c1",
            content="x",
            image=ImageContent(images=[_png(2400, 4800)], dimension_note="tool's own note"),
        )
        _agent()._bound_result_images(result)
        assert result.image.dimension_note == "tool's own note"


class TestAutoResizeSetting:
    def test_disabled_leaves_dimensions_untouched(self):
        result = _result_with([_png(2400, 4800)])
        _agent(auto_resize=False)._bound_result_images(result)
        assert _dimensions(result.image.images[0]) == (2400, 4800)

    def test_defaults_to_enabled_when_settings_are_unavailable(self):
        result = _result_with([_png(2400, 4800)])
        _agent(settings=None)._bound_result_images(result)
        assert max(_dimensions(result.image.images[0])) <= 2000


class TestDegradesGracefully:
    def test_result_without_images_is_untouched(self):
        result = ToolResult(id="c1", content="no media here")
        _agent()._bound_result_images(result)  # must not raise
        assert result.image is None

    def test_empty_image_list_is_untouched(self):
        result = _result_with([])
        _agent()._bound_result_images(result)
        assert result.image.images == []

    def test_urls_pass_through_undecoded(self):
        result = _result_with(["http://example.com/cat.png"])
        _agent()._bound_result_images(result)
        assert result.image.images == ["http://example.com/cat.png"]

    def test_undecodable_image_is_preserved_not_dropped(self):
        """The tool already produced this; failing to process it must not delete it."""
        result = _result_with(["!!! not valid base64 or image data !!!"])
        _agent()._bound_result_images(result)
        assert result.image.images == ["!!! not valid base64 or image data !!!"]

    def test_one_bad_image_does_not_block_its_siblings(self):
        result = _result_with(["@@ garbage @@", _png(3000, 3000)])
        _agent()._bound_result_images(result)
        assert result.image.images[0] == "@@ garbage @@"
        assert max(_dimensions(result.image.images[1])) <= 2000


class TestTruncationKeepsMedia:
    """Truncating *text* must not delete the image attached to the same result."""

    @pytest.mark.asyncio
    async def test_image_survives_text_truncation(self):
        # Over the 50 KB / 2000-line cap, as a browser page dump would be.
        huge = "\n".join(f"line {i} " + "x" * 200 for i in range(3000))
        result = ToolResult(id="c1", content=huge, image=ImageContent(images=[_png(2400, 4800)]))

        out = await _agent()._after_tool_call(invocation=None, result=result, signal=None)

        assert "truncated" in out.content
        assert out.image is not None, "the screenshot was dropped when the text was truncated"
        assert max(_dimensions(out.image.images[0])) <= 2000

    @pytest.mark.asyncio
    async def test_short_output_keeps_its_image_and_bounds_it(self):
        result = ToolResult(id="c1", content="ok", image=ImageContent(images=[_png(2400, 4800)]))
        out = await _agent()._after_tool_call(invocation=None, result=result, signal=None)
        assert out.content == "ok"
        assert max(_dimensions(out.image.images[0])) <= 2000
