"""Third-party components must keep working across the string-renderer change.

Extensions add components via ``layout.header.add_child(...)`` and friends.
``add_child`` type-hints ``Component``, but Python does not enforce it, so two
shapes exist in the wild:

* the documented one — subclasses ``Component``, implements ``render``
* a duck-typed object implementing only ``render``, with no ``Component`` base

Both must render. Either would otherwise raise inside ``TUI._do_render``,
which swallows exceptions — so a third-party extension would present as a
frozen screen with no error shown to the user.

The ``render_cells(area, buf)`` shape is gone along with the cell grid. A
component still on it gets a named TypeError rather than a silent freeze,
which is the point of ``_child_lines`` raising explicitly.
"""

from __future__ import annotations

import pytest

from tau.tui.component import Component, Container
from tau.tui.geometry import Position

WIDTH = 40


class DocumentedWidget(Component):
    """What docs/extensions.md tells authors to write."""

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        return ["documented"]


class DuckTypedWidget:
    """No Component base — containers only ever call render()."""

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        return ["duck typed"]

    def handle_input(self, event: object) -> bool:
        return False

    def invalidate(self) -> None:
        pass


class LegacyCellWidget(Component):
    """The removed contract. Must fail loudly rather than freeze the screen."""

    def render_cells(self, area: object, buf: object) -> int:
        raise AssertionError("should never be called")


class NotRenderable:
    pass


@pytest.mark.parametrize(
    ("widget", "expected"),
    [
        (DocumentedWidget(), "documented"),
        (DuckTypedWidget(), "duck typed"),
    ],
    ids=["documented", "duck-typed"],
)
def test_container_renders_every_extension_shape(widget: object, expected: str) -> None:
    container = Container()
    container.add_child(widget)  # type: ignore[arg-type]
    assert [x.rstrip() for x in container.render(WIDTH)] == [expected]


def test_mixed_extension_shapes_in_one_container() -> None:
    container = Container()
    for w in (DocumentedWidget(), DuckTypedWidget()):
        container.add_child(w)  # type: ignore[arg-type]
    assert [x.rstrip() for x in container.render(WIDTH)] == [
        "documented",
        "duck typed",
    ]


def test_a_non_renderable_child_says_so_clearly() -> None:
    """Better a named TypeError than an AttributeError swallowed into a freeze."""
    container = Container()
    container.add_child(NotRenderable())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="NotRenderable is not renderable"):
        container.render(WIDTH)


def test_a_cells_only_component_fails_loudly() -> None:
    """render_cells is gone; a component still on it must not silently render blank."""
    container = Container()
    container.add_child(LegacyCellWidget())
    with pytest.raises(NotImplementedError, match="LegacyCellWidget"):
        container.render(WIDTH)


def test_duck_typed_widget_without_cursor_support_is_fine() -> None:
    """Components gained cursor_position; a duck-typed object will not have it."""
    container = Container()
    container.add_child(DuckTypedWidget())  # type: ignore[arg-type]
    container.render(WIDTH)
    assert container.cursor_position is None


def test_extension_cursor_still_propagates() -> None:
    class CursorWidget(Component):
        def render(self, width: int) -> list[str]:  # noqa: ARG002
            self.cursor_position = Position(3, 0)
            return ["prompt"]

    container = Container()
    container.add_child(DocumentedWidget())
    container.add_child(CursorWidget())
    container.render(WIDTH)
    assert container.cursor_position == Position(3, 1)
