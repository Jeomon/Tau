"""line_to_ansi is the string-contract counterpart of Buffer.set_line.

Components migrating off render_cells build structured Line/Span content and
wrote it with buf.set_line. This must place text in the same columns, resolve
alignment the same way, and clip the same — otherwise selectors and footers
shift by a column when they migrate.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.compose import line_to_ansi
from tau.tui.geometry import Rect
from tau.tui.layout import Alignment
from tau.tui.style import Style
from tau.tui.text import Line, Span

WIDTH = 30


def _via_set_line(line: Line, width: int, x: int = 0) -> str:
    buf = Buffer.empty(Rect(0, 0, max(1, x + width), 1))
    buf.set_line(x, 0, line, width)
    return row_to_ansi(buf, 0, embed_raw=True, trim_trailing_blanks=True)


def _same_pixels(a: str, b: str, width: int) -> bool:
    pa = Buffer.empty(Rect(0, 0, width, 1))
    pb = Buffer.empty(Rect(0, 0, width, 1))
    parse_ansi_into(pa, 0, 0, a, width)
    parse_ansi_into(pb, 0, 0, b, width)
    return pa.content == pb.content


CASES = {
    "plain": Line([Span("hello")]),
    "styled": Line([Span("hello", Style().dim())]),
    "multi span": Line([Span("a", Style().bold()), Span("b"), Span("c", Style().dim())]),
    "empty": Line([]),
    "empty span": Line([Span("")]),
    "overflows": Line([Span("x" * 80)]),
    "unicode": Line([Span("日本語のテキスト")]),
    "emoji": Line([Span("🎉 party 👨\u200d👩\u200d👧")]),
    "trailing spaces": Line([Span("hi      ")]),
}


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("width", [10, 30, 60])
def test_matches_set_line(name: str, width: int) -> None:
    line = CASES[name]
    assert _same_pixels(line_to_ansi(line, width), _via_set_line(line, width), width)


@pytest.mark.parametrize(
    "alignment", [Alignment.LEFT, Alignment.CENTER, Alignment.RIGHT], ids=lambda a: a.name
)
def test_alignment_matches_set_line(alignment) -> None:
    line = Line([Span("mid")], alignment=alignment)
    assert _same_pixels(line_to_ansi(line, WIDTH), _via_set_line(line, WIDTH), WIDTH)


@pytest.mark.parametrize("x", [0, 1, 4])
def test_x_offset_matches_set_line(x: int) -> None:
    line = Line([Span("shifted")])
    total = x + WIDTH
    assert _same_pixels(line_to_ansi(line, WIDTH, x), _via_set_line(line, WIDTH, x), total)


def test_ported_footer_components_render_the_same_as_before() -> None:
    """The two footer widgets migrated to render(); check they did not shift."""
    from tau.builtins.extensions.footer.git import GitBadge

    c = GitBadge.__new__(GitBadge)
    c._text = "main *2"
    expected = _via_set_line(Line([Span("main *2", Style().dim())]), WIDTH)
    assert _same_pixels(c.render(WIDTH)[0], expected, WIDTH)


def test_selector_controller_with_nothing_active_renders_nothing() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    ctl = SelectorController.__new__(SelectorController)
    ctl._active = None
    assert ctl.render(WIDTH) == []
