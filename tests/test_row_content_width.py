"""Row measures a child's content, not its padding.

The cell path decided where the next alignment group starts by scanning for
the last cell carrying content: a trailing *plain* space is padding, but a
styled one paints a background and counts, as does a skip cell. Measuring
with visible_width instead treats padding as content and shoves the centre
and right groups rightwards.

No existing test covered it -- the whole suite stayed green with the wrong
measurement, because the (width - center_width) // 2 term usually dominates.
It only shows up when the left group is wide enough to win that max().
"""

from __future__ import annotations

from tau.tui.component import Row, StaticComponent, _content_width
from tau.tui.style import Style, apply_style
from tau.tui.utils import strip_ansi


def test_trailing_plain_padding_does_not_push_the_centre_group() -> None:
    left = "aaaaaaaaaaaa   "  # 12 columns of content, 3 of padding
    assert _content_width(left, 20) == 12

    row = Row([(StaticComponent([left]), "left"), (StaticComponent(["C"]), "center")])
    rendered = strip_ansi(row.render(20)[0])
    assert rendered.index("C") == 13


def test_trailing_styled_blanks_do_count_as_content() -> None:
    """A styled space paints a background, so it is content, not padding."""
    left = apply_style(Style().with_bg((80, 0, 0)), "abc   ")
    assert _content_width(left, 20) == 6

    row = Row([(StaticComponent([left]), "left"), (StaticComponent(["C"]), "center")])
    assert strip_ansi(row.render(20)[0]).index("C") == 9


def test_row_is_one_line_tall() -> None:
    """Only the child's first row is placed, as the cell blit did."""
    row = Row([(StaticComponent(["one", "two", "three"]), "left")])
    assert row.render(20) == [*row.render(20)[:1]]
    assert "two" not in strip_ansi("".join(row.render(20)))


def test_rows_clips_and_pads_children_to_their_slot() -> None:
    from tau.tui.component import Rows

    rows = Rows([(StaticComponent(["a"]), 2), (StaticComponent(["b", "c", "d"]), 2)])
    assert [strip_ansi(line) for line in rows.render(10)] == ["a", "", "b", "c"]


def test_columns_places_children_side_by_side() -> None:
    from tau.tui.component import Columns

    cols = Columns([(StaticComponent(["L1", "L2"]), None), (StaticComponent(["R1"]), None)])
    out = [strip_ansi(line) for line in cols.render(20)]
    assert out[0].startswith("L1")
    assert out[0].rstrip().endswith("R1")
    assert out[1].strip() == "L2"
