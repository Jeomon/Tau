"""Regression test: TUI.render_cells must populate _child_rows the same way
render() does.

Renderer.render() (tui.py) calls component.render_cells(...) directly, not
render(width) — so without a render_cells override, TUI would inherit
Container's generic one (which knows nothing about _child_rows), leaving it
permanently empty and silently breaking mouse_position_for (used by Layout
for click-to-select) for the entire life of the app.
"""

from __future__ import annotations

from tau.tui.component import StaticComponent
from tau.tui.input import MouseEvent
from tau.tui.tui import TUI


class FakeTerminal:
    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        return lambda: None


def test_render_cells_populates_child_rows_like_render() -> None:
    term = FakeTerminal()
    tui = TUI(terminal=term)  # type: ignore[arg-type]
    child_a = StaticComponent(["a1", "a2"])
    child_b = StaticComponent(["b1"])
    tui.children.append(child_a)
    tui.children.append(child_b)

    tui._renderer.render(tui)

    assert tui._child_rows[id(child_a)] == 0
    assert tui._child_rows[id(child_b)] == 2


def test_mouse_position_for_resolves_after_real_render_path() -> None:
    term = FakeTerminal()
    tui = TUI(terminal=term)  # type: ignore[arg-type]
    child_a = StaticComponent(["a1", "a2"])
    child_b = StaticComponent(["b1"])
    tui.children.append(child_a)
    tui.children.append(child_b)

    tui._renderer.render(tui)

    result = tui.mouse_position_for(child_b, MouseEvent(x=1, y=4, button=0, pressed=True))
    assert result is not None
