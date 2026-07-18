"""Regression: AutocompletePicker label columns are measured in terminal
columns, not code points — CJK/emoji labels must not shift or clip the
description column."""

from __future__ import annotations

from tau.tui.autocomplete import AutocompleteItem, AutocompletePicker
from tau.tui.utils import strip_ansi, visible_width
from tests.render_helpers import render_cells_to_lines as _lines


def test_wide_labels_do_not_shift_the_description_column() -> None:
    picker = AutocompletePicker(max_visible=5)
    picker.set_items(
        [
            AutocompleteItem("日本語のラベルとても長い", "desc-a"),
            AutocompleteItem("short", "desc-b"),
        ]
    )
    lines = _lines(picker, 40)
    assert len(lines) == 2

    def desc_col(line: str, desc: str) -> int:
        stripped = strip_ansi(line)
        return visible_width(stripped[: stripped.index(desc)])

    assert desc_col(lines[0], "desc-a") == desc_col(lines[1], "desc-b")
