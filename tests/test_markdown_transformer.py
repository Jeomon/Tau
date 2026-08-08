"""Tests for the markdown transformer hook.

Transformers are deliberately applied to settled messages only. The parse
cache (`_parse_markdown`) and the streaming renderer are both keyed on the
text, so rewriting per frame would make every flushed token a fresh cache key
— for a result the reader sees for one flush before it is replaced. These pin
that boundary down along with the registry's failure behaviour.
"""

from __future__ import annotations

import re

import pytest

from tau.message.types import AssistantMessage, TextContent
from tau.modes.interactive.components.message_list import MessageBlock
from tau.tui.markdown import markdown_transformer_registry
from tau.tui.utils import strip_ansi

WIDTH = 80


@pytest.fixture(autouse=True)
def _clear_registry():
    markdown_transformer_registry.replace([])
    yield
    markdown_transformer_registry.replace([])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(contents=[TextContent(content=text)])


def _rendered(text: str, *, streaming: bool = False) -> str:
    block = MessageBlock(_assistant(text), streaming=streaming)
    return "\n".join(strip_ansi(line) for line in block.render(WIDTH))


# ── Registry ─────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_transformers_run_in_registration_order(self) -> None:
        markdown_transformer_registry.replace(
            [lambda t: t + " one", lambda t: t + " two"],
        )
        assert markdown_transformer_registry.apply("start") == "start one two"

    def test_each_sees_the_previous_output(self) -> None:
        markdown_transformer_registry.replace(
            [lambda t: t.replace("a", "b"), lambda t: t.replace("b", "c")],
        )
        assert markdown_transformer_registry.apply("a") == "c"

    def test_a_failing_transformer_is_skipped(self) -> None:
        def boom(_text: str) -> str:
            raise RuntimeError("extension bug")

        markdown_transformer_registry.replace([boom, lambda t: t + "!"])

        # The survivor still runs: one broken extension must not cost the
        # message, nor the other extensions' rewrites.
        assert markdown_transformer_registry.apply("text") == "text!"

    def test_a_non_string_return_is_ignored(self) -> None:
        """A transformer that forgets to return keeps the text it was given,
        rather than replacing the message with `None`."""

        def forgets_to_return(_text: str):
            return None

        markdown_transformer_registry.replace(
            [forgets_to_return, lambda t: t + "!"]  # type: ignore[list-item]
        )
        assert markdown_transformer_registry.apply("text") == "text!"

    def test_empty_registry_is_falsy_and_a_no_op(self) -> None:
        assert not markdown_transformer_registry
        assert markdown_transformer_registry.apply("untouched") == "untouched"

    def test_replace_drops_the_previous_set(self) -> None:
        markdown_transformer_registry.replace([lambda t: t + " old"])
        markdown_transformer_registry.replace([lambda t: t + " new"])
        assert markdown_transformer_registry.apply("x") == "x new"


# ── Render path ──────────────────────────────────────────────────────────────


class TestRenderPath:
    def test_settled_message_is_transformed(self) -> None:
        markdown_transformer_registry.replace(
            [lambda t: re.sub(r"#(\d+)", r"issue \1", t)],
        )
        assert "issue 42" in _rendered("see #42 for details")

    def test_streaming_message_is_not_transformed(self) -> None:
        """Live text renders as the model sent it; the rewrite lands when the
        turn settles. Transforming here would rewrite the cache key on every
        flushed token."""
        markdown_transformer_registry.replace([lambda t: t.replace("raw", "rewritten")])
        assert "raw" in _rendered("raw text", streaming=True)

    def test_the_same_text_settles_to_the_transformed_form(self) -> None:
        markdown_transformer_registry.replace([lambda t: t.replace("raw", "rewritten")])

        assert "raw" in _rendered("raw text", streaming=True)
        assert "rewritten" in _rendered("raw text")

    def test_no_transformers_leaves_rendering_untouched(self) -> None:
        markdown_transformer_registry.replace([])
        assert "plain text" in _rendered("plain text")

    def test_markdown_added_by_a_transformer_is_parsed(self) -> None:
        """The rewrite happens before parsing, so a transformer can introduce
        markup and not just text."""
        markdown_transformer_registry.replace([lambda t: t.replace("TODO", "**TODO**")])
        out = _rendered("TODO check this")
        # Bold is rendered as a style, so the marker itself must be gone.
        assert "**TODO**" not in out
        assert "TODO" in out
