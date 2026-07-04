"""Regression tests: a legacy component embedding an Image's rendered line
(containing a raw Kitty/iTerm2 escape) must not have that escape silently
dropped when parsed into cells by the generic Component.render_cells bridge.

parse_ansi_into's normal scan treats any escape it doesn't recognize (SGR,
OSC-8 hyperlink) as an opaque, discardable no-op — correct for decorative
codes, wrong for an image payload. This covers the fix.
"""

from __future__ import annotations

from unittest.mock import patch

import tau.tui.terminal as term_mod
from tau.tui.ansi_bridge import parse_ansi_into
from tau.tui.buffer import Buffer
from tau.tui.component import Container, StaticComponent
from tau.tui.components.image import Image
from tau.tui.geometry import Rect


class _FakeCell:
    width_px = 8
    height_px = 16


def _render_legacy_image_lines(protocol: str | None) -> list[str]:
    class _FakeCaps:
        images = protocol

    with (
        patch.object(term_mod, "get_capabilities", return_value=_FakeCaps()),
        patch.object(term_mod, "get_cell_dimensions", return_value=_FakeCell()),
    ):
        img = Image(b"\x89PNG fakebytes", "image/png")
        img._dims.width_px, img._dims.height_px = 400, 200
        return img.render(40)


def test_kitty_escape_survives_generic_bridge_via_container() -> None:
    lines = _render_legacy_image_lines("kitty")
    container = Container()
    container.add_child(StaticComponent(lines))

    buf = Buffer.empty(Rect(0, 0, 40, 0))
    container.render_cells(Rect(0, 0, 40, 0), buf)

    assert len(buf.raw_writes) == 1
    assert buf.raw_writes[0].data.startswith("\x1b_G")


def test_iterm2_escape_survives_generic_bridge_via_container() -> None:
    """iTerm2's line leads with a cursor-up move before the OSC 1337 escape."""
    lines = _render_legacy_image_lines("iterm2")
    container = Container()
    container.add_child(StaticComponent(lines))

    buf = Buffer.empty(Rect(0, 0, 40, 0))
    container.render_cells(Rect(0, 0, 40, 0), buf)

    assert len(buf.raw_writes) == 1
    assert "\x1b]1337;File=" in buf.raw_writes[0].data


def test_fallback_text_has_no_raw_writes() -> None:
    lines = _render_legacy_image_lines(None)
    container = Container()
    container.add_child(StaticComponent(lines))

    buf = Buffer.empty(Rect(0, 0, 40, 0))
    container.render_cells(Rect(0, 0, 40, 0), buf)

    assert buf.raw_writes == []


def test_parse_ansi_into_returns_col_unchanged_for_image_line() -> None:
    buf = Buffer.empty(Rect(0, 0, 40, 1))
    end_col = parse_ansi_into(buf, 5, 0, "\x1b_Gfake\x1b\\", 40)
    assert end_col == 5
    assert buf.raw_writes[0].x == 5
    assert buf.raw_writes[0].y == 0
