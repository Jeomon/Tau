"""Third-party components must keep working across the string-renderer change.

Extensions add components via ``layout.header.add_child(...)`` and friends.
``add_child`` type-hints ``Component``, but Python does not enforce it, so
three shapes exist in the wild:

* the documented one — subclasses ``Component``, implements ``render_cells``
* the new one — subclasses ``Component``, implements ``render``
* a duck-typed object implementing only ``render_cells``, which worked before
  because containers only ever called that

All three must render. The third would otherwise raise inside
``TUI._do_render``, which swallows exceptions — so a third-party extension
would present as a frozen screen with no error shown to the user.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_wrapped_into
from tau.tui.buffer import Buffer
from tau.tui.component import Component, Container
from tau.tui.geometry import Position, Rect

WIDTH = 40


class DocumentedWidget(Component):
    """What docs/extensions.md tells authors to write."""

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        return parse_ansi_wrapped_into(buf, area.x, area.y, "documented", area.width)


class ModernWidget(Component):
    """The contract everything is moving to."""

    def render(self, width: int) -> list[str]:
        return ["modern"]


class DuckTypedWidget:
    """No Component base — worked before because containers called render_cells."""

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        return parse_ansi_wrapped_into(buf, area.x, area.y, "duck typed", area.width)

    def handle_input(self, event: object) -> bool:
        return False

    def invalidate(self) -> None:
        pass


class NotRenderable:
    pass


@pytest.mark.parametrize(
    ("widget", "expected"),
    [
        (DocumentedWidget(), "documented"),
        (ModernWidget(), "modern"),
        (DuckTypedWidget(), "duck typed"),
    ],
    ids=["documented", "modern", "duck-typed"],
)
def test_container_renders_every_extension_shape(widget: object, expected: str) -> None:
    container = Container()
    container.add_child(widget)  # type: ignore[arg-type]
    assert [x.rstrip() for x in container.render(WIDTH)] == [expected]


def test_mixed_extension_shapes_in_one_container() -> None:
    container = Container()
    for w in (DocumentedWidget(), DuckTypedWidget(), ModernWidget()):
        container.add_child(w)  # type: ignore[arg-type]
    assert [x.rstrip() for x in container.render(WIDTH)] == [
        "documented",
        "duck typed",
        "modern",
    ]


def test_every_shape_also_renders_through_the_cell_contract() -> None:
    """A not-yet-migrated parent renders extension children into cells."""
    container = Container()
    for w in (DocumentedWidget(), DuckTypedWidget(), ModernWidget()):
        container.add_child(w)  # type: ignore[arg-type]
    buf = Buffer.empty(Rect(0, 0, WIDTH, 0))
    assert container.render_cells(Rect(0, 0, WIDTH, 0), buf) == 3


def test_a_non_renderable_child_says_so_clearly() -> None:
    """Better a named TypeError than an AttributeError swallowed into a freeze."""
    container = Container()
    container.add_child(NotRenderable())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="NotRenderable is not renderable"):
        container.render(WIDTH)


def test_duck_typed_widget_without_cursor_support_is_fine() -> None:
    """Components gained cursor_position; a duck-typed object will not have it."""
    container = Container()
    container.add_child(DuckTypedWidget())  # type: ignore[arg-type]
    container.render(WIDTH)
    assert container.cursor_position is None


def test_extension_cursor_still_propagates() -> None:
    class CursorWidget(Component):
        def render(self, width: int) -> list[str]:
            self.cursor_position = Position(3, 0)
            return ["prompt"]

    container = Container()
    container.add_child(DocumentedWidget())
    container.add_child(CursorWidget())
    container.render(WIDTH)
    assert container.cursor_position == Position(3, 1)
