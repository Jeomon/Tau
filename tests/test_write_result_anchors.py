"""The write tool shows hashline anchors, like read and edit.

It used to number its output `1, 2, 3…`. A bare index is not what `read` or
`edit` address a line by, so a file you had just written came back looking like
a different kind of object from the same file read a moment later — and the
number shown was not usable to edit it.

The anchors are computed from the written content with the same `stamp_lines`
that `read` uses, so what a write displays is exactly what a read of that file
displays.
"""

from __future__ import annotations

from typing import Any

import pytest

from tau.builtins.tools.read import _render_read_result
from tau.builtins.tools.utils import stamp_lines
from tau.builtins.tools.write import _render_write_result
from tau.tui.utils import strip_ansi

_LINES = ["def hello():", "    print('hi')", "", "def hello():", "    print('hi')"]


class _WriteOpts:
    def __init__(self, lines: list[str], created: bool = False, is_error: bool = False) -> None:
        self.is_error = is_error
        self.metadata: dict[str, Any] = {
            "total_lines": len(lines),
            "created": created,
            "lines": lines,
        }


class _ReadOpts:
    def __init__(self, count: int) -> None:
        self.is_error = False
        self.metadata: dict[str, Any] = {"lines_returned": count}


def _write_rows(lines: list[str], **kw: Any) -> list[str]:
    return [strip_ansi(row) for row in _render_write_result("ok", _WriteOpts(lines, **kw))]


def _read_rows(lines: list[str]) -> list[str]:
    tokens = stamp_lines(lines)
    content = "\n".join(
        f"{i}:{h}|{t}" for i, (h, t) in enumerate(zip(tokens, lines, strict=True), 1)
    )
    return [strip_ansi(row) for row in _render_read_result(content, _ReadOpts(len(lines)))]


def test_rows_carry_an_anchor_not_a_bare_index() -> None:
    rows = _write_rows(_LINES)

    assert rows[1].startswith("1:"), rows[1]
    assert not rows[1].startswith("1  ")


def test_the_anchors_match_a_read_of_the_same_content() -> None:
    """Same file, same anchors — that is the whole point of the change."""
    write_rows = _write_rows(_LINES)
    read_rows = _read_rows(_LINES)

    assert write_rows[1:] == read_rows[1:]


def test_duplicate_lines_get_distinct_anchors() -> None:
    """`stamp_lines` disambiguates repeats; a plain index never had to."""
    rows = _write_rows(_LINES)

    first = rows[1].split()[0]
    duplicate = rows[4].split()[0]
    assert first.split(":")[1] != duplicate.split(":")[1]


def test_the_header_is_unchanged() -> None:
    assert _write_rows(_LINES)[0] == "Written 5 lines"
    assert _write_rows(_LINES, created=True)[0] == "Created 5 lines"


def test_one_line_is_singular() -> None:
    assert _write_rows(["only"])[0] == "Written 1 line"


def test_an_empty_write_renders_only_the_header() -> None:
    assert _write_rows([]) == ["Written 0 lines"]


def test_an_error_is_passed_through_untouched() -> None:
    opts = _WriteOpts([], is_error=True)

    rows = _render_write_result("Cannot write file: denied", opts)

    assert rows == ["Cannot write file: denied"]


@pytest.mark.parametrize("count", [1, 2, 40])
def test_every_line_is_anchored(count: int) -> None:
    lines = [f"line {i}" for i in range(count)]

    rows = _write_rows(lines)[1:]

    assert len(rows) == count
    assert all(":" in row.split()[0] for row in rows)
