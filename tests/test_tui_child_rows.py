"""Regression test: TUI.render_cells must populate _child_rows the same way
render() does.

Renderer.render() (service.py) calls component.render_cells(...) directly, not
render(width) — so without a render_cells override, TUI would inherit
Container's generic one (which knows nothing about _child_rows), leaving it
permanently empty and silently breaking mouse_position_for (used by Layout
for click-to-select) for the entire life of the app.
"""

from __future__ import annotations

from tau.tui.component import StaticComponent
from tau.tui.input import MouseEvent
from tau.tui.service import TUI


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


class _FrozenChild(StaticComponent):
    """Minimal render_split_cells-capable child, mirroring MessageList's contract."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(lines)
        self.frozen_generation = 0

    def render_split_cells(self, width: int):
        from tau.tui.buffer import Buffer
        from tau.tui.geometry import Rect

        buf = Buffer.empty(Rect(0, 0, width, 0))
        self.render_cells(Rect(0, 0, width, 0), buf)
        return buf, []


def test_remove_child_forgets_frozen_row_cache_state() -> None:
    """Without pruning, _child_frozen_gen/_child_row_cache only ever grow, and a
    GC'd child's id() can be reused by an unrelated later object — which would
    then spuriously hit this stale cache entry on its very first render.

    remove_child() calls _request_render(), which needs a running loop (fine
    in the live app; matches the asyncio.run() wrapping used elsewhere for
    this reason — see _make() in test_tui_frozen_row_cache.py).
    """
    import asyncio

    async def _run() -> None:
        term = FakeTerminal()
        tui = TUI(terminal=term)  # type: ignore[arg-type]
        frozen = _FrozenChild(["x1", "x2"])
        tui.children.append(frozen)

        tui._renderer.render(tui)
        key = id(frozen)
        assert key in tui._child_row_cache

        tui.remove_child(frozen)

        assert key not in tui._child_rows
        assert key not in tui._child_frozen_gen
        assert key not in tui._child_row_cache

    asyncio.run(_run())


def test_clear_forgets_all_child_state() -> None:
    import asyncio

    async def _run() -> None:
        term = FakeTerminal()
        tui = TUI(terminal=term)  # type: ignore[arg-type]
        frozen = _FrozenChild(["x1", "x2"])
        tui.children.append(frozen)
        tui._renderer.render(tui)
        assert tui._child_row_cache

        tui.clear()

        assert tui._child_rows == {}
        assert tui._child_frozen_gen == {}
        assert tui._child_row_cache == {}

    asyncio.run(_run())
