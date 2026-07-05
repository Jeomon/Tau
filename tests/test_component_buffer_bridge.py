from tau.tui.ansi_bridge import parse_ansi_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.component import (
    Column,
    Columns,
    Component,
    Constrained,
    Container,
    Row,
    Rows,
    StaticComponent,
    Text,
)
from tau.tui.components.box import Box
from tau.tui.frame import _diff_row_cells
from tau.tui.geometry import Rect
from tau.tui.style import Style, apply_style
from tests.render_helpers import render_cells_to_lines as _render_via_cells


class _CellsOnly(Component):
    """A Buffer-native component implementing the sole render_cells contract."""

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        buf.grow_to(area.y + 1)
        buf.set_string(area.x, area.y, "native row", Style().italic())
        return 1


def _rstrip_all(lines: list[str]) -> list[str]:
    # render_cells always pads to the full buffer width, so trailing
    # whitespace isn't meaningful — ignore it for comparison.
    return [line.rstrip() for line in lines]


def test_container_renders_mixed_native_children() -> None:
    container = Container()
    container.add_child(_CellsOnly())
    container.add_child(StaticComponent(["static row"]))

    lines = _rstrip_all(_render_via_cells(container, 30))
    assert len(lines) == 2
    assert lines[1] == "static row"


def test_column_renders_mixed_native_children() -> None:
    column = Column([_CellsOnly(), StaticComponent(["static row"])])

    lines = _rstrip_all(_render_via_cells(column, 30))
    assert len(lines) == 2
    assert lines[1] == "static row"


def test_box_composes_a_buffer_native_child() -> None:
    content = "boxed-" + ("x" * 40) + "-tail"
    box = Box(StaticComponent([content]), padding_x=1)

    lines = _rstrip_all(_render_via_cells(box, 20))

    assert len(lines) > 1
    assert content in "".join(line.strip() for line in lines)


def test_core_components_own_native_render_paths() -> None:
    for component_type in (StaticComponent, Text, Row, Constrained, Columns, Rows):
        assert component_type.render_cells is not Component.render_cells


def test_ansi_bridge_round_trip_preserves_style_and_double_width() -> None:
    buf = Buffer.empty(Rect(0, 0, 20, 1))
    line = "hi " + apply_style(Style().bold().with_fg((10, 20, 30)), "你好") + " end"
    parse_ansi_into(buf, 0, 0, line, 20)
    flattened = row_to_ansi(buf, 0)

    buf2 = Buffer.empty(Rect(0, 0, 20, 1))
    parse_ansi_into(buf2, 0, 0, flattened, 20)

    for x in range(20):
        c1, c2 = buf.get(x, 0), buf2.get(x, 0)
        assert c1.symbol == c2.symbol
        assert c1.style == c2.style


def test_ansi_bridge_double_width_glyph_does_not_duplicate_column() -> None:
    from tau.tui.utils import visible_width

    buf = Buffer.empty(Rect(0, 0, 10, 1))
    parse_ansi_into(buf, 0, 0, apply_style(Style().italic(), "你好"), 10)
    out = row_to_ansi(buf, 0)
    assert visible_width(out) == 10


def test_ansi_bridge_closes_hyperlink_before_plain_cells() -> None:
    buf = Buffer.empty(Rect(0, 0, 8, 1))
    buf.set_string(0, 0, "link", Style().with_link("https://example.com"))
    buf.set_string(4, 0, " end")

    out = row_to_ansi(buf, 0)

    assert "\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\\x1b[0m end" in out


def test_cell_diff_closes_hyperlink_before_plain_cells() -> None:
    previous = Buffer.empty(Rect(0, 0, 8, 1))
    current = Buffer.empty(Rect(0, 0, 8, 1))
    current.set_string(0, 0, "link", Style().with_link("https://example.com"))
    current.set_string(4, 0, " end")

    out = _diff_row_cells(previous, current, 0, 8)

    assert "\x1b]8;;https://example.com\x1b\\link" in out
    assert "\x1b]8;;\x1b\\\x1b[0mend" in out
