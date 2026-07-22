"""Tests for the built-in footer model badge."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tau.builtins.extensions.footer.model import ModelBadge
from tau.tui.utils import strip_ansi
from tests.render_helpers import render_cells_to_lines


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_tokens_include_cache_read: bool = False


@dataclass
class _Response:
    usage: _Usage


class _Context:
    def __init__(self, tokens: int = 100, context_window: int = 1_000) -> None:
        self.tokens = tokens
        self.context_window = context_window

    def get_context_usage(self) -> dict[str, int]:
        return {
            "tokens": self.tokens,
            "context_window": self.context_window,
        }


def test_response_usage_updates_badge_immediately() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")
    response = _Response(_Usage(input_tokens=200, output_tokens=50))

    badge.update_context_from_response(response, _Context())

    assert "25%" in strip_ansi(render_cells_to_lines(badge, 80)[0])


def test_response_usage_includes_cache_tokens() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")
    response = _Response(
        _Usage(
            input_tokens=100,
            output_tokens=25,
            cache_read_tokens=300,
            cache_write_tokens=75,
        )
    )

    badge.update_context_from_response(response, _Context(context_window=1_000))

    assert "50%" in strip_ansi(render_cells_to_lines(badge, 80)[0])


def test_response_usage_does_not_double_count_inclusive_cache_tokens() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")
    response = _Response(
        _Usage(
            input_tokens=500,
            output_tokens=100,
            cache_read_tokens=400,
            input_tokens_include_cache_read=True,
        )
    )

    badge.update_context_from_response(response, _Context(context_window=1_000))

    assert "60%" in strip_ansi(render_cells_to_lines(badge, 80)[0])


def test_missing_response_usage_falls_back_to_context() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")

    badge.update_context_from_response(object(), _Context(tokens=400, context_window=1_000))

    assert "40%" in strip_ansi(render_cells_to_lines(badge, 80)[0])


# ── Extension reload lifecycle ────────────────────────────────────────────────


class _Footer:
    def __init__(self) -> None:
        self.children: list[object] = []

    def add_child(self, child: object) -> None:
        self.children.append(child)

    def remove_child(self, child: object) -> None:
        if child in self.children:
            self.children.remove(child)


class _Tui:
    def request_render(self) -> None:
        pass


class _Layout:
    def __init__(self) -> None:
        self.footer = _Footer()
        self._tui = _Tui()


class _ExtCtx:
    """Just enough ExtensionContext surface for the footer handlers."""

    def __init__(self, layout: _Layout) -> None:
        self._layout = layout
        self.has_ui = True
        self.cwd = "."
        self.settings = None
        self.model_id = "m"
        self.provider_id = "p"
        self.model_thinking = True


def _load_footer_extension():
    from tau.builtins.extensions.footer import register
    from tau.extensions.api import Extension, ExtensionAPI

    ext = Extension(path="footer-test")
    register(ExtensionAPI(extension=ext, llm=None, settings=None, cwd="."))  # type: ignore[arg-type]
    return ext


def _fire(ext, event_type: str, ctx: _ExtCtx) -> None:
    import types

    for handler in ext.handlers.get(event_type, []):
        handler(types.SimpleNamespace(type=event_type), ctx)


@pytest.mark.asyncio
async def test_reload_swaps_footer_row_instead_of_orphaning_it() -> None:
    """extension_unload must detach the old row and extension_reloaded must
    mount the new one — otherwise the visible badges freeze after any reload
    (project trust, /reload) because their handlers were unsubscribed."""
    layout = _Layout()
    ctx = _ExtCtx(layout)

    old = _load_footer_extension()
    _fire(old, "tui_ready", ctx)
    assert len(layout.footer.children) == 1
    old_row = layout.footer.children[0]

    _fire(old, "extension_unload", ctx)
    assert layout.footer.children == []

    new = _load_footer_extension()
    _fire(new, "extension_reloaded", ctx)
    assert len(layout.footer.children) == 1
    assert layout.footer.children[0] is not old_row
    _fire(new, "extension_unload", ctx)


@pytest.mark.asyncio
async def test_reloaded_mount_is_idempotent() -> None:
    layout = _Layout()
    ctx = _ExtCtx(layout)

    ext = _load_footer_extension()
    _fire(ext, "tui_ready", ctx)
    _fire(ext, "extension_reloaded", ctx)

    assert len(layout.footer.children) == 1
    _fire(ext, "extension_unload", ctx)
