"""Regression test: replacing/removing a focused footer or widget must not
leave TUI._focused dangling on a detached component.

self.footer/widgets_above/widgets_below are plain Container instances
(component.py) — their clear()/remove_child() have no notion of TUI focus,
since only TUI._focused tracks that. Extension footer factories are handed
the live tui object (set_footer's `factory(tui, theme)` signature) precisely
so they can build interactive footers that call tui.set_focus(self) on
themselves. Without Layout reclaiming focus before removal, a later
set_footer()/set_widget()/remove_widget() call would leave TUI._focused
pointing at a component no longer in the render tree — dispatch tries the
focused component before falling through to global handlers, so this
silently swallows all keyboard input into a widget that's no longer on
screen.
"""

from __future__ import annotations

import asyncio

from tau.modes.interactive.components.layout import Layout
from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.service import TUI


class FakeTerminal:
    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        return lambda: None


class _EatsEverything(Component):
    """Stand-in for an interactive extension widget that consumes all input."""

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        return 0

    def handle_input(self, event: object) -> bool:
        return True


def _make_layout() -> tuple[TUI, Layout]:
    term = FakeTerminal()
    tui = TUI(terminal=term)  # type: ignore[arg-type]
    layout = Layout(tui, cursor_blink=False)
    return tui, layout


def _run(coro_fn) -> None:
    """Run a test body under asyncio.run(): Layout mutators call
    tui.request_render(), which needs a running loop (see the same pattern
    documented in tests/test_tui_frozen_row_cache.py's _make()).
    """

    async def _wrapped() -> None:
        coro_fn()

    asyncio.run(_wrapped())


def test_set_footer_reclaims_focus_from_replaced_footer() -> None:
    def _body() -> None:
        tui, layout = _make_layout()
        old_footer = _EatsEverything()
        layout.set_footer(old_footer)
        tui.set_focus(old_footer)  # mirrors an extension's factory(tui, theme) self-focusing
        assert tui._focused is old_footer

        layout.set_footer(_EatsEverything())

        assert tui._focused is not old_footer
        assert tui._focused is layout

    _run(_body)


def test_set_footer_none_reclaims_focus() -> None:
    def _body() -> None:
        tui, layout = _make_layout()
        old_footer = _EatsEverything()
        layout.set_footer(old_footer)
        tui.set_focus(old_footer)

        layout.set_footer(None)

        assert tui._focused is not old_footer
        assert tui._focused is layout

    _run(_body)


def test_set_widget_reclaims_focus_from_replaced_widget() -> None:
    def _body() -> None:
        tui, layout = _make_layout()
        old_widget = _EatsEverything()
        layout.set_widget("picker", old_widget)
        tui.set_focus(old_widget)

        layout.set_widget("picker", _EatsEverything())

        assert tui._focused is not old_widget
        assert tui._focused is layout

    _run(_body)


def test_remove_widget_reclaims_focus() -> None:
    def _body() -> None:
        tui, layout = _make_layout()
        widget = _EatsEverything()
        layout.set_widget("picker", widget, placement="below_editor")
        tui.set_focus(widget)

        layout.remove_widget("picker")

        assert tui._focused is not widget
        assert tui._focused is layout

    _run(_body)


def test_reclaim_does_not_disturb_unrelated_focus() -> None:
    """If some other component holds focus (not the removed one), leave it alone."""

    def _body() -> None:
        tui, layout = _make_layout()
        old_footer = _EatsEverything()
        layout.set_footer(old_footer)
        tui.set_focus(layout.input)

        layout.set_footer(_EatsEverything())

        assert tui._focused is layout.input

    _run(_body)
