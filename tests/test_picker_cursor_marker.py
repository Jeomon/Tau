"""The inline pickers mark the focused row with the selector arrow.

`/` commands, `@` files and the command palette all render through
SelectListTheme, so one `selector_arrow` key drives every cursor marker —
matching the selector lists and the input prompt.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.theme import LayoutTheme, SelectListTheme

WIDTH = 80


def _rows(component, height: int = 20) -> list[str]:
    buf = Buffer.empty(Rect(0, 0, WIDTH, height))
    written = component.render_cells(Rect(0, 0, WIDTH, height), buf)
    return [
        "".join(buf.get(x, y).symbol for x in range(WIDTH)).rstrip() for y in range(written)
    ]


def _autocomplete(theme=None):
    from tau.tui.autocomplete import AutocompleteItem, AutocompletePicker

    picker = AutocompletePicker(theme=theme)
    picker.set_items(
        [
            AutocompleteItem(label="/new", description="Start a fresh session"),
            AutocompleteItem(label="/fork", description="Branch the session tree"),
            AutocompleteItem(label="/clear", description="Clear the message list"),
        ]
    )
    return picker


class TestInlinePicker:
    def test_the_focused_row_carries_the_arrow(self):
        rows = _rows(_autocomplete())

        assert rows[0].startswith("❯ /new")
        assert not rows[1].lstrip().startswith("❯")

    def test_only_one_row_is_marked(self):
        rows = _rows(_autocomplete())
        assert sum(r.count("❯") for r in rows) == 1

    def test_the_marker_follows_the_selection(self):
        picker = _autocomplete()
        picker.move_down()
        rows = _rows(picker)

        assert rows[1].startswith("❯ /fork")
        assert not rows[0].startswith("❯")

    def test_labels_stay_in_one_column(self):
        rows = _rows(_autocomplete())
        focused = rows[0].index("/new")
        unfocused = rows[1].index("/fork")

        assert focused == unfocused

    def test_an_empty_arrow_renders_a_marker_less_list(self):
        theme = SelectListTheme()
        theme.selector_arrow = ""
        rows = _rows(_autocomplete(theme))

        assert "❯" not in "\n".join(rows)
        # Alignment is still uniform without a marker.
        assert rows[0].index("/new") == rows[1].index("/fork")


class TestThemeWiring:
    def test_layout_theme_mirrors_its_arrow_onto_the_pickers(self):
        theme = LayoutTheme()
        theme.selector_arrow = "▶"
        theme.__post_init__()

        assert theme.select_list.selector_arrow == "▶"
        assert theme.select_list.arrow == theme.accent

    def test_a_custom_glyph_reaches_the_rendered_rows(self):
        theme = LayoutTheme()
        theme.selector_arrow = "▶"
        theme.__post_init__()
        rows = _rows(_autocomplete(theme.select_list))

        assert rows[0].startswith("▶ /new")


class TestFilePickerHasNoMarker:
    """`@` is a plain file list — the cursor there is the highlight alone.

    Only the command list (`/`) carries the arrow; adding one here made the
    file names read as a menu rather than a path listing.
    """

    def test_the_file_picker_renders_no_arrow(self, tmp_path):
        from tau.modes.interactive.components.file_picker import FilePicker

        (tmp_path / "alpha.py").write_text("")
        (tmp_path / "beta.py").write_text("")
        picker = FilePicker(cwd=tmp_path)
        picker.open()

        assert "❯" not in "\n".join(_rows(picker))
