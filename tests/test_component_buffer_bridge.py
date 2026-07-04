from tau.tui.ansi_bridge import parse_ansi_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.component import Column, Component, Container, StaticComponent
from tau.tui.frame import _diff_row_cells
from tau.tui.geometry import Rect
from tau.tui.style import Style, apply_style


class _LegacyOnly(Component):
    """Implements only the old render(width) contract."""

    def render(self, width: int) -> list[str]:
        return ["legacy plain", apply_style(Style().bold().with_fg("red"), "legacy styled")]


class _CellsOnly(Component):
    """Implements only the new render_cells(area, buf) contract."""

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        buf.grow_to(area.y + 1)
        buf.set_string(area.x, area.y, "native row", Style().italic())
        return 1


def _render_via_cells(component: Component, width: int) -> list[str]:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    used = component.render_cells(Rect(0, 0, width, 0), buf)
    return [row_to_ansi(buf, y) for y in range(used)]


def _rstrip_all(lines: list[str]) -> list[str]:
    # render_cells always pads to the full buffer width; render(width) does
    # not — trailing whitespace differs between the two paths but is not
    # visually distinguishable on a terminal, so ignore it for comparison.
    return [line.rstrip() for line in lines]


def test_legacy_component_default_render_cells_matches_render() -> None:
    c = _LegacyOnly()
    assert _rstrip_all(_render_via_cells(c, 30)) == _rstrip_all(c.render(30))


def test_cells_only_component_default_render_matches_render_cells() -> None:
    c = _CellsOnly()
    assert _rstrip_all(c.render(30)) == _rstrip_all(_render_via_cells(c, 30))


def test_container_mixes_legacy_and_cells_components_identically() -> None:
    container = Container()
    container.add_child(_LegacyOnly())
    container.add_child(_CellsOnly())
    container.add_child(StaticComponent(["static row"]))

    via_render = container.render(30)
    via_cells = _render_via_cells(container, 30)
    assert _rstrip_all(via_render) == _rstrip_all(via_cells)
    assert len(via_render) == 4


def test_column_mixes_legacy_and_cells_components_identically() -> None:
    column = Column([_LegacyOnly(), _CellsOnly(), StaticComponent(["static row"])])

    via_render = column.render(30)
    via_cells = _render_via_cells(column, 30)
    assert _rstrip_all(via_render) == _rstrip_all(via_cells)


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
