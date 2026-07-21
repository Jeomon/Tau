"""Tests for tau/tui/components/multi_select_list.py."""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.components.multi_select_list import (
    CHECKED_SYMBOL,
    UNCHECKED_SYMBOL,
    MultiSelectItem,
    MultiSelectList,
)
from tau.tui.geometry import Rect
from tau.tui.input import KeyEvent


def _key(name: str) -> KeyEvent:
    return KeyEvent(key=name, char=None)


def _list(labels=("Web", "CLI", "Mobile"), **kwargs):
    done: list = []
    component = MultiSelectList(
        title="Which surfaces?",
        items=[MultiSelectItem(label=label) for label in labels],
        on_done=done.append,
        **kwargs,
    )
    return component, done


def _render(component, width: int = 70) -> str:
    buf = Buffer.empty(Rect(0, 0, width, 40))
    rows = component.render_cells(Rect(0, 0, width, 40), buf)
    return "\n".join(
        "".join(buf.get(x, y).symbol for x in range(width)).rstrip() for y in range(rows)
    )


class TestSelection:
    def test_space_toggles_the_row_under_the_cursor(self):
        c, done = _list()

        c.handle_input(_key("space"))
        c.handle_input(_key("down"))
        c.handle_input(_key("down"))
        c.handle_input(_key("space"))
        c.handle_input(_key("enter"))

        assert done == [["Web", "Mobile"]]

    def test_toggling_twice_unchecks(self):
        c, done = _list()

        c.handle_input(_key("space"))
        c.handle_input(_key("space"))
        c.handle_input(_key("enter"))

        assert done == [[]]

    def test_empty_result_is_not_a_cancel(self):
        c, done = _list()

        c.handle_input(_key("enter"))

        assert done == [[]]
        assert done[0] is not None  # [] and None must stay distinguishable

    def test_escape_cancels(self):
        c, done = _list()

        c.handle_input(_key("space"))
        c.handle_input(_key("escape"))

        assert done == [None]

    def test_cursor_wraps_around(self):
        c, done = _list()

        c.handle_input(_key("up"))  # wraps to the last row
        c.handle_input(_key("space"))
        c.handle_input(_key("enter"))

        assert done == [["Mobile"]]

    def test_values_can_differ_from_labels(self):
        done: list = []
        c = MultiSelectList(
            title="t",
            items=[
                MultiSelectItem(label="Web app", value={"id": 1}),
                MultiSelectItem(label="CLI", value={"id": 2}),
            ],
            on_done=done.append,
        )

        c.handle_input(_key("space"))
        c.handle_input(_key("enter"))

        assert done == [[{"id": 1}]]

    def test_preselected_items_come_back(self):
        done: list = []
        c = MultiSelectList(
            title="t",
            items=[MultiSelectItem(label="Web", checked=True), MultiSelectItem(label="CLI")],
            on_done=done.append,
        )

        c.handle_input(_key("enter"))

        assert done == [["Web"]]

    def test_unknown_keys_are_not_consumed(self):
        c, _ = _list()
        assert c.handle_input(_key("f5")) is False

    def test_an_empty_list_consumes_nothing(self):
        done: list = []
        c = MultiSelectList(title="t", items=[], on_done=done.append)
        assert c.handle_input(_key("enter")) is False
        assert done == []


class TestRequireSelection:
    def test_enter_is_refused_while_nothing_is_ticked(self):
        c, done = _list(require_selection=True)

        c.handle_input(_key("enter"))

        assert done == []
        assert "at least one" in _render(c)

    def test_enter_works_once_something_is_ticked(self):
        c, done = _list(require_selection=True)

        c.handle_input(_key("enter"))  # refused
        c.handle_input(_key("space"))
        c.handle_input(_key("enter"))

        assert done == [["Web"]]


class TestRender:
    def test_shows_a_box_per_row_and_the_title(self):
        c, _ = _list()
        out = _render(c)

        assert "Which surfaces?" in out
        assert out.count(UNCHECKED_SYMBOL) == 3
        assert CHECKED_SYMBOL not in out

    def test_ticked_rows_show_a_check(self):
        c, _ = _list()
        c.handle_input(_key("space"))
        out = _render(c)

        assert CHECKED_SYMBOL in out
        assert out.count(UNCHECKED_SYMBOL) == 2

    def test_hint_counts_the_selection(self):
        c, _ = _list()
        assert "selected" not in _render(c)

        c.handle_input(_key("space"))
        assert "1 selected" in _render(c)

    def test_descriptions_are_rendered(self):
        done: list = []
        c = MultiSelectList(
            title="t",
            items=[MultiSelectItem(label="Web", description="the browser app")],
            on_done=done.append,
        )
        assert "the browser app" in _render(c)


class TestDescriptionRows:
    def test_a_long_description_wraps_onto_its_own_rows(self):
        done: list = []
        c = MultiSelectList(
            title="t",
            items=[
                MultiSelectItem(
                    label="Web app",
                    description="the browser client, including the admin console and more text",
                ),
                MultiSelectItem(label="CLI"),
            ],
            on_done=done.append,
        )

        out = _render(c, width=54)
        lines = [line for line in out.splitlines() if line.strip()]

        # The description occupies rows of its own beneath the label…
        label_row = next(i for i, line in enumerate(lines) if "Web app" in line)
        assert "the browser client" in lines[label_row + 1]
        # …and is not truncated away.
        assert "more text" in out
        # The next item still renders after it.
        assert any("CLI" in line for line in lines)

    def test_descriptions_do_not_push_later_items_off(self):
        done: list = []
        items = [
            MultiSelectItem(label=f"opt{i}", description="a description long enough to wrap once")
            for i in range(3)
        ]
        c = MultiSelectList(title="t", items=items, on_done=done.append)

        out = _render(c, width=50)

        for i in range(3):
            assert f"opt{i}" in out
