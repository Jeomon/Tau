"""Tool-produced images must be bounded before they enter the context window.

Two ways media in a session can break every subsequent request:

1. Oversized images. Providers validate every image in a request, not just the
   newest: Anthropic drops its per-image cap from 8000px to 2000px once a
   request carries many, so an oversized screenshot accepted early starts
   failing *every* later request. `read`'s byte cap is no defence — a 3 MB PNG
   can still be 8000px wide. Only the interactive paste path used to call
   `process_image`; `read`, browser and desktop screenshots, and any extension
   tool went through unprocessed.

2. Media the active model cannot accept at all. Switching models with `/model`
   leaves earlier images, audio, video or files in the transcript, and the
   provider rejects the whole payload every turn from then on.

Either way the media is persisted, so the failure survives reload and the
session is stuck until it is started over.
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


class _Model:
    def __init__(self, name, input_modalities):
        self.name = name
        self.input = input_modalities


def _agent_with_model(model) -> Agent:
    agent = Agent.__new__(Agent)
    agent._engine = type("_E", (), {"llm": type("_L", (), {"model": model})(), "_settings": None})()
    return agent


def _vision():
    from tau.inference.model.types import Modality

    return _Model("claude-sonnet-4-6", [Modality.Text, Modality.Image])


def _text_only():
    from tau.inference.model.types import Modality

    return _Model("deepseek-v4-flash", [Modality.Text])


class TestUnsupportedMediaDropped:
    """Regression: an image already in history outlives the model that accepted it.

    Switching to a text-only model with /model leaves the image in every later
    request; the provider rejects the whole payload, so the turn fails — and
    fails again next turn, because the image is in the transcript. The session
    is stuck until it is started over. A wrong `input` entry in a model catalog
    causes the same loop with no switching at all.
    """

    def _user_with_image(self):
        from tau.message.types import ImageContent, TextContent, UserMessage

        return UserMessage(
            contents=[TextContent(content="what is this?"), ImageContent(images=[_png(50, 50)])]
        )

    def _tool_result_with_image(self):
        from tau.message.types import ImageContent, ToolMessage, ToolResultContent

        return ToolMessage(
            contents=[
                ToolResultContent(
                    id="c1", content="screenshot taken", image=ImageContent(images=[_png(50, 50)])
                )
            ]
        )

    def _kinds(self, message):
        return [type(c).__name__ for c in message.contents]

    # ── Text-only model: images must go ───────────────────────────────────────

    def test_user_image_is_replaced_with_a_note(self):
        out = _agent_with_model(_text_only())._drop_unsupported_media([self._user_with_image()])
        assert self._kinds(out[0]) == ["TextContent", "TextContent"]
        assert "does not accept image input" in out[0].contents[1].content
        assert "deepseek-v4-flash" in out[0].contents[1].content

    def test_tool_result_image_is_detached_and_noted(self):
        out = _agent_with_model(_text_only())._drop_unsupported_media(
            [self._tool_result_with_image()]
        )
        result = out[0].contents[0]
        assert result.image is None
        assert result.content.startswith("screenshot taken")
        assert "does not accept image input" in result.content

    def test_surrounding_text_is_preserved(self):
        out = _agent_with_model(_text_only())._drop_unsupported_media([self._user_with_image()])
        assert out[0].contents[0].content == "what is this?"

    # ── The stored transcript must not be touched ─────────────────────────────

    def test_original_message_is_left_intact(self):
        """Switching back to a vision model has to restore the image."""
        from tau.message.types import ImageContent

        original = self._user_with_image()
        _agent_with_model(_text_only())._drop_unsupported_media([original])
        assert any(isinstance(c, ImageContent) for c in original.contents)

    def test_original_tool_result_is_left_intact(self):
        original = self._tool_result_with_image()
        _agent_with_model(_text_only())._drop_unsupported_media([original])
        assert original.contents[0].image is not None

    # ── Vision model: nothing changes ─────────────────────────────────────────

    def test_vision_model_keeps_images(self):
        from tau.message.types import ImageContent

        out = _agent_with_model(_vision())._drop_unsupported_media([self._user_with_image()])
        assert any(isinstance(c, ImageContent) for c in out[0].contents)

    def test_messages_without_media_are_not_copied(self):
        from tau.message.types import TextContent, UserMessage

        message = UserMessage(contents=[TextContent(content="plain")])
        out = _agent_with_model(_text_only())._drop_unsupported_media([message])
        assert out[0] is message

    # ── Unknown capabilities: do not degrade ──────────────────────────────────

    def test_unknown_model_leaves_the_request_alone(self):
        from tau.message.types import ImageContent

        agent = Agent.__new__(Agent)
        agent._engine = type("_E", (), {"llm": None})()
        out = agent._drop_unsupported_media([self._user_with_image()])
        assert any(isinstance(c, ImageContent) for c in out[0].contents)

    def test_model_with_empty_input_list_leaves_the_request_alone(self):
        from tau.message.types import ImageContent

        out = _agent_with_model(_Model("mystery", []))._drop_unsupported_media(
            [self._user_with_image()]
        )
        assert any(isinstance(c, ImageContent) for c in out[0].contents)


class TestEveryModalityIsHandled:
    """Images were only the reported case. A text-only model is equally unable
    to accept audio, video or a file, and each wedges a session identically.
    """

    def _modality(self, name):
        from tau.inference.model.types import Modality

        return getattr(Modality, name)

    def _all_input(self):
        from tau.inference.model.types import Modality

        return list(Modality)

    def _content(self, kind):
        from tau.message.types import AudioContent, FileContent, ImageContent, VideoContent

        blob = _png(20, 20)
        return {
            "Image": lambda: ImageContent(images=[blob]),
            "Audio": lambda: AudioContent(audios=[blob]),
            "Video": lambda: VideoContent(videos=[blob]),
            "File": lambda: FileContent(files=[blob]),
        }[kind]()

    @pytest.mark.parametrize("kind", ["Image", "Audio", "Video", "File"])
    def test_unsupported_modality_is_replaced_with_a_note(self, kind):
        from tau.message.types import TextContent, UserMessage

        # A model that accepts everything *except* this one modality.
        accepted = [m for m in self._all_input() if m is not self._modality(kind)]
        message = UserMessage(contents=[TextContent(content="look"), self._content(kind)])

        out = _agent_with_model(_Model("partial", accepted))._drop_unsupported_media([message])

        assert [type(c).__name__ for c in out[0].contents] == ["TextContent", "TextContent"]
        assert f"does not accept {kind.lower()} input" in out[0].contents[1].content

    @pytest.mark.parametrize("kind", ["Image", "Audio", "Video", "File"])
    def test_supported_modality_is_left_alone(self, kind):
        from tau.message.types import UserMessage

        message = UserMessage(contents=[self._content(kind)])
        out = _agent_with_model(_Model("full", self._all_input()))._drop_unsupported_media(
            [message]
        )
        assert out[0] is message

    @pytest.mark.parametrize("slot", ["image", "audio", "video"])
    def test_tool_result_media_slots_are_cleared(self, slot):
        """ToolResultContent carries media in fields, not in `contents`."""
        from tau.message.types import ToolMessage, ToolResultContent

        kind = slot.capitalize()
        accepted = [m for m in self._all_input() if m is not self._modality(kind)]
        result = ToolResultContent(id="c1", content="captured", **{slot: self._content(kind)})

        out = _agent_with_model(_Model("partial", accepted))._drop_unsupported_media(
            [ToolMessage(contents=[result])]
        )

        assert getattr(out[0].contents[0], slot) is None
        assert f"does not accept {slot} input" in out[0].contents[0].content
        assert getattr(result, slot) is not None  # transcript untouched

    def test_several_modalities_dropped_in_one_pass(self):
        from tau.message.types import ToolMessage, ToolResultContent

        result = ToolResultContent(
            id="c1",
            content="captured",
            image=self._content("Image"),
            audio=self._content("Audio"),
            video=self._content("Video"),
        )
        out = _agent_with_model(_text_only())._drop_unsupported_media(
            [ToolMessage(contents=[result])]
        )
        dropped = out[0].contents[0]
        assert (dropped.image, dropped.audio, dropped.video) == (None, None, None)
        for word in ("image", "audio", "video"):
            assert f"does not accept {word} input" in dropped.content

    def test_only_the_unsupported_modality_is_dropped(self):
        """A model that sees images but not audio keeps the image."""
        from tau.message.types import ToolMessage, ToolResultContent

        accepted = [m for m in self._all_input() if m is not self._modality("Audio")]
        result = ToolResultContent(
            id="c1", content="x", image=self._content("Image"), audio=self._content("Audio")
        )
        out = _agent_with_model(_Model("no-audio", accepted))._drop_unsupported_media(
            [ToolMessage(contents=[result])]
        )
        assert out[0].contents[0].image is not None
        assert out[0].contents[0].audio is None


class TestEveryMessagePathIsCovered:
    """The filter has to reach every message shape that can carry media."""

    def _img_user(self):
        from tau.message.types import ImageContent, TextContent, UserMessage

        return UserMessage(
            contents=[TextContent(content="hi"), ImageContent(images=[_png(20, 20)])]
        )

    def _has_media(self, message) -> bool:
        from tau.message.types import ImageContent, ToolResultContent

        for c in message.contents:
            if isinstance(c, ImageContent):
                return True
            if isinstance(c, ToolResultContent) and c.image is not None:
                return True
        return False

    def test_user_message(self):
        out = _agent_with_model(_text_only())._drop_unsupported_media([self._img_user()])
        assert not self._has_media(out[0])

    def test_tool_message(self):
        from tau.message.types import ImageContent, ToolMessage, ToolResultContent

        msg = ToolMessage(
            contents=[
                ToolResultContent(id="c1", content="cap", image=ImageContent(images=[_png(20, 20)]))
            ]
        )
        out = _agent_with_model(_text_only())._drop_unsupported_media([msg])
        assert not self._has_media(out[0])

    def test_custom_message(self):
        """CustomMessage is outside the LLMMessage union but carries contents too."""
        from tau.message.types import CustomMessage, ImageContent

        msg = CustomMessage(custom_type="x", contents=[ImageContent(images=[_png(20, 20)])])
        out = _agent_with_model(_text_only())._drop_unsupported_media([msg])
        assert not self._has_media(out[0])
        assert type(out[0]).__name__ == "CustomMessage"  # class preserved

    def test_message_classes_are_preserved(self):
        from tau.message.types import ImageContent, ToolMessage, ToolResultContent, UserMessage

        agent = _agent_with_model(_text_only())
        tool_msg = ToolMessage(
            contents=[
                ToolResultContent(id="c1", content="cap", image=ImageContent(images=[_png(20, 20)]))
            ]
        )
        # dataclasses.replace must hand back the same class, not a base type —
        # providers dispatch on the message role.
        assert isinstance(agent._drop_unsupported_media([self._img_user()])[0], UserMessage)
        assert isinstance(agent._drop_unsupported_media([tool_msg])[0], ToolMessage)
        assert agent._drop_unsupported_media([self._img_user()])[0].role is UserMessage().role

    @pytest.mark.asyncio
    async def test_ephemeral_extension_messages_are_filtered(self):
        """Engine._run appends these *after* transform_context (engine/service.py:785),
        so they are the one path that would otherwise still reach the provider.
        """
        from tau.hooks.engine import ContextEventResult
        from tau.hooks.service import Hooks

        agent = _agent_with_model(_text_only())
        agent._pending_session_ctx = type("_C", (), {"messages": []})()
        agent.hooks = Hooks()

        injected = self._img_user()

        async def inject(_event):
            return ContextEventResult(ephemeral_messages=[injected])

        agent.hooks.register("context", inject)

        out = await agent._ephemeral_injection()

        assert out, "the ephemeral message should still be delivered"
        assert not self._has_media(out[0]), "extension-injected media reached the provider"
        assert self._has_media(injected), "the extension's own object must not be mutated"
