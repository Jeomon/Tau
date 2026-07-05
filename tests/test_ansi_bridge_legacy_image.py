"""Regression tests: Image's rendered RawWrite payload must not be dropped or
mangled, whether written directly via render_cells (the live path) or
embedded verbatim in a raw-ANSI content string parsed by parse_ansi_into
(the path used by components that ingest arbitrary ANSI text, e.g.
TextOverlay).

parse_ansi_into's normal scan treats any escape it doesn't recognize (SGR,
OSC-8 hyperlink) as an opaque, discardable no-op — correct for decorative
codes, wrong for an image payload. This covers the fix.
"""

from __future__ import annotations

from unittest.mock import patch

import tau.tui.terminal as term_mod
from tau.tui.ansi_bridge import parse_ansi_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.components.image import Image, ImageOptions
from tau.tui.geometry import Rect


class _FakeCell:
    width_px = 8
    height_px = 16


def test_kitty_render_cells_writes_raw_escape() -> None:
    class _FakeCaps:
        images = "kitty"

    with (
        patch.object(term_mod, "get_capabilities", return_value=_FakeCaps()),
        patch.object(term_mod, "get_cell_dimensions", return_value=_FakeCell()),
    ):
        img = Image(b"\x89PNG fakebytes", "image/png")
        img._dims.width_px, img._dims.height_px = 400, 200
        buf = Buffer.empty(Rect(0, 0, 40, 0))
        img.render_cells(Rect(0, 0, 40, 0), buf)

    assert len(buf.raw_writes) == 1
    assert buf.raw_writes[0].data.startswith("\x1b_G")


def test_iterm2_render_cells_writes_raw_escape() -> None:
    """iTerm2's line leads with a cursor-up move before the OSC 1337 escape."""

    class _FakeCaps:
        images = "iterm2"

    with (
        patch.object(term_mod, "get_capabilities", return_value=_FakeCaps()),
        patch.object(term_mod, "get_cell_dimensions", return_value=_FakeCell()),
    ):
        img = Image(b"\x89PNG fakebytes", "image/png")
        img._dims.width_px, img._dims.height_px = 400, 200
        buf = Buffer.empty(Rect(0, 0, 40, 0))
        img.render_cells(Rect(0, 0, 40, 0), buf)

    assert len(buf.raw_writes) == 1
    assert "\x1b]1337;File=" in buf.raw_writes[0].data


def test_fallback_protocol_has_no_raw_writes() -> None:
    class _FakeCaps:
        images = None

    with (
        patch.object(term_mod, "get_capabilities", return_value=_FakeCaps()),
        patch.object(term_mod, "get_cell_dimensions", return_value=_FakeCell()),
    ):
        img = Image(b"\x89PNG fakebytes", "image/png")
        img._dims.width_px, img._dims.height_px = 400, 200
        buf = Buffer.empty(Rect(0, 0, 40, 0))
        img.render_cells(Rect(0, 0, 40, 0), buf)

    assert buf.raw_writes == []


def test_direct_fallback_image_wraps_long_filename() -> None:
    class _FakeCaps:
        images = None

    filename = "screenshot-" + ("x" * 40) + ".png"
    with patch.object(term_mod, "get_capabilities", return_value=_FakeCaps()):
        image = Image(
            b"\x89PNG fakebytes",
            "image/png",
            options=ImageOptions(filename=filename),
        )
        buf = Buffer.empty(Rect(0, 0, 20, 0))
        rows = image.render_cells(Rect(0, 0, 20, 0), buf)

    assert rows > 1
    rendered = "".join(row_to_ansi(buf, row).strip() for row in range(rows))
    assert filename in rendered


def test_parse_ansi_into_returns_col_unchanged_for_image_line() -> None:
    """A raw image escape embedded in an arbitrary ANSI content string (e.g.
    from an extension-supplied TextOverlay line) must survive parse_ansi_into
    as an atomic RawWrite rather than being silently dropped as an
    unrecognized escape."""
    buf = Buffer.empty(Rect(0, 0, 40, 1))
    end_col = parse_ansi_into(buf, 5, 0, "\x1b_Gfake\x1b\\", 40)
    assert end_col == 5
    assert buf.raw_writes[0].x == 5
    assert buf.raw_writes[0].y == 0
