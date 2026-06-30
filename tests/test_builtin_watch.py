"""Tests for the built-in watch extension."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tau.builtins.extensions.watch import _build_context, _parse_vtt, register


def test_parse_vtt_returns_timestamped_deduplicated_text() -> None:
    content = """WEBVTT

00:00:01.000 --> 00:00:02.000
<c>Hello</c>

00:00:02.000 --> 00:00:03.000
Hello

00:01:04.000 --> 00:01:05.000
Next line
"""

    assert _parse_vtt(content) == "[0:01] Hello\n[1:04] Next line"


def test_build_context_includes_metadata_and_missing_caption_notice() -> None:
    context = _build_context(
        "https://example.com/video",
        {
            "title": "Example",
            "channel": "Author",
            "duration": "1:23",
            "description": "Description",
            "transcript": "",
        },
    )

    assert "Title: Example" in context
    assert "Channel: Author" in context
    assert "No transcript available" in context


def test_register_adds_idle_watch_command() -> None:
    captured: dict[str, Any] = {}

    def register_command(
        name: str,
        description: str,
        handler: Any,
        **kwargs: Any,
    ) -> None:
        captured.update(
            name=name,
            description=description,
            handler=handler,
            kwargs=kwargs,
        )

    register(SimpleNamespace(register_command=register_command))  # type: ignore[arg-type]

    assert captured["name"] == "watch"
    assert captured["kwargs"]["argument_hint"] == "<video-url> [question]"
    assert "requires_idle" not in captured["kwargs"]
