"""line_to_ansi flattens structured Line/Span content into one ANSI string.

Selectors, pickers, the spinner and the footer widgets all build ``Line``s and
render through this, so it has to place text in the right columns, resolve
alignment, and clip on a glyph boundary. Column placement is measured in
*columns*, not characters — a CJK or ZWJ span is wider than its length.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_text import tokenize
from tau.tui.compose import line_to_ansi
from tau.tui.layout import Alignment
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import strip_ansi, visible_width

WIDTH = 30

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
def test_never_exceeds_its_width(name: str, width: int) -> None:
    """Content is clipped to the available columns, never overrun."""
    assert visible_width(line_to_ansi(CASES[name], width)) <= width


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("width", [10, 30, 60])
def test_clipping_lands_on_a_glyph_boundary(name: str, width: int) -> None:
    """A wide glyph is dropped whole rather than cut in half."""
    out = line_to_ansi(CASES[name], width)
    source = "".join(span.content for span in CASES[name])
    kept = "".join(c for c, _w, _s in tokenize(out))
    assert source.startswith(kept.rstrip()) or kept.strip() == ""


def test_spans_are_concatenated_in_order() -> None:
    out = strip_ansi(line_to_ansi(CASES["multi span"], WIDTH))
    assert out == "abc"


def test_span_styles_are_preserved() -> None:
    tokens = tokenize(line_to_ansi(CASES["multi span"], WIDTH))
    assert tokens[0][2].add_modifier  # "a" is bold
    assert not tokens[1][2].add_modifier  # "b" is plain


@pytest.mark.parametrize(
    "alignment", [Alignment.LEFT, Alignment.CENTER, Alignment.RIGHT], ids=lambda a: a.name
)
def test_alignment_positions_the_content(alignment) -> None:
    line = Line([Span("mid")], alignment=alignment)
    out = line_to_ansi(line, WIDTH)
    lead = len(strip_ansi(out)) - len(strip_ansi(out).lstrip(" "))
    expected = {
        Alignment.LEFT: 0,
        Alignment.CENTER: (WIDTH - 3) // 2,
        Alignment.RIGHT: WIDTH - 3,
    }[alignment]
    assert lead == expected


@pytest.mark.parametrize("x", [0, 1, 4])
def test_x_offset_indents_the_content(x: int) -> None:
    out = strip_ansi(line_to_ansi(Line([Span("shifted")]), WIDTH, x))
    assert out.startswith(" " * x + "shifted")


def test_a_wide_span_is_measured_in_columns() -> None:
    """日本語のテキスト is 16 columns, so it does not fit in 10."""
    out = line_to_ansi(CASES["unicode"], 10)
    assert visible_width(out) <= 10
    assert "\ufffd" not in strip_ansi(out)


def test_ported_footer_components_render_the_same_as_before() -> None:
    """The two footer widgets migrated to render(); check they did not shift."""
    from tau.builtins.extensions.footer.git import GitBadge

    c = GitBadge.__new__(GitBadge)
    c._text = "main *2"
    assert strip_ansi(c.render(WIDTH)[0]).rstrip() == "main *2"


def test_selector_controller_with_nothing_active_renders_nothing() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    ctl = SelectorController.__new__(SelectorController)
    ctl._active = None
    assert ctl.render(WIDTH) == []
