"""The picker's question is a heading, not a column beside the first option.

``UIContext.select`` used to pass its title as ``SelectItem(description=...)``
on item 0, because ``SelectList`` had no title of its own. That put "Approve
this terminal command?" on the same row as "Allow once", reading as though the
question described that one choice rather than the whole picker — the inverse
of what a title means. The permissions extension documented the constraint and
worked around it by keeping every headline artificially short.
"""

from __future__ import annotations

from tau.tui.components.select_list import SelectItem, SelectList
from tau.tui.style import Style
from tau.tui.utils import strip_ansi
from tests.render_helpers import render_to_lines as _lines

_OPTIONS = ["Allow once", "Allow for this session (echo*)", "Deny"]
_QUESTION = "Approve this terminal command?"


def _plain(component, width: int = 60) -> list[str]:
    return [strip_ansi(line).rstrip() for line in _lines(component, width)]


def _picker(title: str = _QUESTION) -> SelectList[str]:
    return SelectList(
        [SelectItem(label=o, value=o) for o in _OPTIONS],
        max_visible=5,
        title=title,
    )


def _is_rule(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= {"─"}


def test_the_question_is_its_own_row_above_the_options() -> None:
    lines = _plain(_picker())

    assert lines[0].strip() == _QUESTION
    assert _is_rule(lines[1]), "a rule closes the heading before the choices start"
    assert lines[2].strip().endswith("Allow once")


def test_the_question_never_shares_a_row_with_an_option() -> None:
    """The regression itself: title and first option on one line."""
    for line in _plain(_picker()):
        assert not (_QUESTION in line and "Allow once" in line)


def test_every_option_still_renders_under_the_heading() -> None:
    body = " ".join(_plain(_picker())[2:])
    for option in _OPTIONS:
        assert option in body


def test_no_heading_rows_without_a_title() -> None:
    """Untitled pickers (file picker, command palette) must be unchanged."""
    lines = _plain(_picker(title=""))

    assert lines[0].strip().endswith("Allow once")
    assert len(lines) == len(_OPTIONS)


def test_line_count_accounts_for_the_heading() -> None:
    titled, plain = _picker(), _picker(title="")

    assert titled.line_count == plain.line_count + 2
    assert titled.line_count == len(_plain(titled))


def test_the_heading_survives_a_filter_that_matches_nothing() -> None:
    """Otherwise "no matches" appears with nothing saying what was asked."""
    picker = _picker()
    picker.set_query("zzzzz")
    lines = _plain(picker)

    assert lines[0].strip() == _QUESTION
    assert "no matches" in lines[-1]


def test_a_long_heading_wraps_and_never_overruns_the_width() -> None:
    """Wrapped, not clipped: the detail is usually the point.

    An over-wide row would soft-wrap in the terminal instead, which desyncs
    the renderer's relative cursor moves for every row below it.
    """
    picker = _picker(title="Approve this? " + "x" * 200)

    assert all(len(line) <= 40 for line in _plain(picker, width=40))


def test_a_multi_line_title_keeps_its_own_line_breaks() -> None:
    """A picker renders between the editor's two dividers, so a caller with
    supporting detail has nowhere else to put it that stays inside the frame."""
    picker = _picker(title="Approve this terminal command?\n\n  command   echo hi")
    lines = _plain(picker)

    assert lines[0].strip() == _QUESTION
    assert lines[1] == "", "the caller's own blank line is preserved"
    assert lines[2].strip() == "command   echo hi"
    assert _is_rule(lines[3]), "the rule closes the block before the choices"
    assert lines[4].strip().endswith("Allow once")


def test_the_rule_marks_where_selectable_content_starts() -> None:
    """Everything above it is static text; everything below responds to keys."""
    picker = _picker(title="Question?\ndetail one\ndetail two")
    lines = _plain(picker)

    index = next(i for i, line in enumerate(lines) if _is_rule(line))
    above, below = lines[:index], lines[index + 1 :]

    assert not any(option in " ".join(above) for option in _OPTIONS)
    assert all(any(option in line for line in below) for option in _OPTIONS)
    assert sum(1 for line in lines if _is_rule(line)) == 1, "exactly one break"


def test_the_detail_lines_are_not_styled_as_headings() -> None:
    """Only line 0 is the question; the rest is body text and must not shout."""
    from tau.tui.theme import SelectListTheme

    theme = SelectListTheme()
    theme.title = Style().bold()
    theme.normal_desc = Style().with_fg("bright_black")
    picker = SelectList(
        [SelectItem(label=o, value=o) for o in _OPTIONS],
        theme=theme,
        title="Question?\ndetail row",
    )
    raw = _lines(picker, 60)

    assert raw[0] != strip_ansi(raw[0]), "the question carries the heading style"
    assert "bright_black" not in raw[0]


def test_a_pre_styled_line_keeps_its_own_colours() -> None:
    """A caller's diff must not be flattened to the body style.

    Reds and greens are most of what makes a write/edit approval reviewable,
    and this component has no business repainting someone else's content.
    """
    from tau.tui.style import apply_style

    green = apply_style(Style().with_fg("bright_green"), "+ added line")
    picker = _picker(title=f"Approve writing this file?\n{green}")
    raw = _lines(picker, 60)

    body = next(line for line in raw if "added line" in line)
    assert "\x1b[92m" in body, "the caller's green survived"
    assert body.count("\x1b[") == green.count("\x1b["), "no extra style was layered on"


def test_a_plain_line_beside_a_styled_one_is_still_themed() -> None:
    """Pass-through is per line, not all-or-nothing for the whole block."""
    from tau.tui.style import apply_style

    green = apply_style(Style().with_fg("bright_green"), "+ added")
    picker = _picker(title=f"Question?\nplain row\n{green}")
    raw = _lines(picker, 60)

    plain = next(line for line in raw if "plain row" in line)
    assert plain != strip_ansi(plain), "an unstyled detail row still gets the body style"
