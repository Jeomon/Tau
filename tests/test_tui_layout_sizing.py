"""Tests for the fixed-width layout primitives: Constrained and Columns."""

from __future__ import annotations

from tau.tui.component import Columns, Constrained, Rows, StaticComponent
from tau.tui.utils import visible_width
from tests.render_helpers import render_cells_to_lines as _lines


def _rstrip_all(lines: list[str]) -> list[str]:
    # render_cells always pads to the full buffer width — trailing whitespace
    # isn't meaningful for content-equality checks below.
    return [line.rstrip() for line in lines]

# ── Constrained ───────────────────────────────────────────────────────────────


def test_constrained_absolute_width_left():
    c = Constrained(StaticComponent(["hi"]), width=10, align="left")
    lines = _lines(c, 40)
    assert len(lines) == 1
    # full line spans the parent width
    assert visible_width(lines[0]) == 40
    # content sits at the very left
    assert lines[0].startswith("hi")
    # 10-col rectangle then trailing fill
    assert lines[0] == "hi" + " " * 8 + " " * 30


def test_constrained_right_align_pins_to_right_edge():
    c = Constrained(StaticComponent(["hi"]), width=10, align="right")
    lines = _lines(c, 40)
    # content hugs the right edge (rectangle right-justified, content right in it)
    assert lines[0].rstrip().endswith("hi")
    assert visible_width(lines[0]) == 40


def test_constrained_percentage_width():
    c = Constrained(StaticComponent(["x"]), width="25%")
    lines = _lines(c, 40)  # 25% of 40 = 10
    assert visible_width(lines[0]) == 40
    # the solid block is 10 wide, rest is alignment fill — total still 40
    assert lines[0].startswith("x" + " " * 9)


def test_constrained_truncates_overflow():
    c = Constrained(StaticComponent(["abcdefghij"]), width=5)
    lines = _lines(c, 20)
    # first 5 cols carry the (ellipsised) content, never more than target
    assert visible_width(lines[0]) == 20
    assert "f" not in lines[0]  # tail was truncated away


def test_constrained_multiline():
    c = Constrained(StaticComponent(["aa", "bbbb"]), width=6, align="left")
    lines = _lines(c, 20)
    assert len(lines) == 2
    assert all(visible_width(line) == 20 for line in lines)


# ── Columns ───────────────────────────────────────────────────────────────────


def test_columns_two_fixed_with_gap():
    left = StaticComponent(["L"])
    right = StaticComponent(["R"])
    cols = Columns([(left, 5), (right, 5)], gap=2)
    lines = _lines(cols, 40)
    assert len(lines) == 1
    # 5 + 2 gap + 5 = 12 visible cols
    assert lines[0][:12] == "L" + " " * 4 + "  " + "R" + " " * 4


def test_columns_flex_fills_remainder():
    sidebar = StaticComponent(["S"])
    main = StaticComponent(["M"])
    cols = Columns([(sidebar, 10), (main, None)], gap=1)
    lines = _lines(cols, 40)
    # sidebar 10 + gap 1 + flex 29 = 40
    assert visible_width(lines[0]) == 40
    assert lines[0].startswith("S" + " " * 9 + " " + "M")


def test_columns_equalizes_height():
    tall = StaticComponent(["1", "2", "3"])
    short = StaticComponent(["a"])
    cols = Columns([(tall, 4), (short, 4)], gap=1)
    lines = _lines(cols, 20)
    assert len(lines) == 3  # tallest column wins
    # the short column is blank-padded on rows 2 and 3
    assert lines[1].startswith("2")
    assert lines[1].endswith(" ")


def test_columns_percentage_split():
    a = StaticComponent(["a"])
    b = StaticComponent(["b"])
    cols = Columns([(a, "50%"), (b, "50%")], gap=0)
    lines = _lines(cols, 20)
    # 50% of usable(20) = 10 each
    assert lines[0][0] == "a"
    assert lines[0][10] == "b"


def test_columns_truncates_when_overflowing_parent():
    a = StaticComponent(["aaaaaa"])
    b = StaticComponent(["bbbbbb"])
    cols = Columns([(a, 6), (b, 6)], gap=2)  # 6+2+6 = 14
    lines = _lines(cols, 8)  # narrower than content → final truncate
    assert visible_width(lines[0]) <= 8


def test_columns_empty_renders_nothing():
    assert _lines(Columns([]), 40) == []


def test_columns_exported_from_package():
    from tau.tui import Columns as PublicColumns
    from tau.tui import Constrained as PublicConstrained

    assert PublicColumns is Columns
    assert PublicConstrained is Constrained


# ── Rows ──────────────────────────────────────────────────────────────────────


def test_rows_budget_fixed_flex_fixed():
    header = StaticComponent(["H"])
    body = StaticComponent(["b1", "b2"])
    footer = StaticComponent(["F"])
    rows = Rows([(header, 1), (body, None), (footer, 1)], height=10)
    lines = _rstrip_all(_lines(rows, 20))
    # 1 + flex(8) + 1 = exactly 10 lines
    assert len(lines) == 10
    assert lines[0] == "H"
    assert lines[1] == "b1"
    assert lines[2] == "b2"
    assert lines[9] == "F"
    # flex body padded with blank lines between its content and the footer
    assert lines[8] == ""


def test_rows_percentage_split():
    top = StaticComponent(["t"])
    bottom = StaticComponent(["b"])
    rows = Rows([(top, "50%"), (bottom, "50%")], height=10)
    lines = _rstrip_all(_lines(rows, 20))
    assert len(lines) == 10
    assert lines[0] == "t"
    assert lines[5] == "b"  # second 50% block starts at line 5


def test_rows_truncates_tall_child_to_row_height():
    tall = StaticComponent(["1", "2", "3", "4", "5"])
    rows = Rows([(tall, 2)], height=2)
    lines = _rstrip_all(_lines(rows, 10))
    assert lines == ["1", "2"]


def test_rows_gap_inserts_blank_lines():
    a = StaticComponent(["a"])
    b = StaticComponent(["b"])
    rows = Rows([(a, 1), (b, 1)], height=3, gap=1)
    lines = _rstrip_all(_lines(rows, 10))
    assert lines == ["a", "", "b"]


def test_rows_no_budget_caps_absolute_keeps_natural():
    # absolute row padded up to its height; flex row keeps natural height
    short = StaticComponent(["x"])
    natural = StaticComponent(["n1", "n2", "n3"])
    rows = Rows([(short, 3), (natural, None)])
    lines = _rstrip_all(_lines(rows, 10))
    # short padded to 3 lines, natural kept at 3 → 6 total
    assert len(lines) == 6
    assert lines[0] == "x"
    assert lines[1] == "" and lines[2] == ""
    assert lines[3:] == ["n1", "n2", "n3"]


def test_rows_empty_renders_nothing():
    assert _lines(Rows([]), 10) == []


def test_rows_exported_from_package():
    from tau.tui import Rows as PublicRows

    assert PublicRows is Rows
