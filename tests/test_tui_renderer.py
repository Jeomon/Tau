"""Tests for tau/tui/tui.py — the Renderer wrapper's integration with the app.

Renderer itself is now a thin wrapper delegating to ScrollbackTerminal
(tau/tui/frame.py) — the differential-render invariants (only changed rows
repaint, viewport clamping, resize, dispose) are exercised directly against
that engine in tests/test_scrollback_terminal.py. What's tested here is
specific to the wrapper: that it correctly builds a Buffer from a
component's render_cells, composites overlays as a real Buffer blit, and
that TUI's lifecycle (dispose, resize-callback bookkeeping) still holds.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.component import Component, StaticComponent
from tau.tui.geometry import Rect
from tau.tui.tui import TUI, OverlayEntry, OverlayOptions, Renderer


class FakeTerminal:
    """Minimal stand-in for tau.tui.terminal.Terminal, capturing writes."""

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []
        self.resize_callbacks: list[object] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        self.resize_callbacks.append(callback)

        def unsubscribe() -> None:
            self.resize_callbacks.remove(callback)

        return unsubscribe


def _content_writes(term: FakeTerminal) -> list[str]:
    return [w for w in term.writes if w != "\x1b[?25l"]


class _CellsComponent(Component):
    """A Buffer-native component (render_cells only), for mixed-tree checks."""

    def __init__(self, text: str) -> None:
        self._text = text

    def render_cells(self, area, buf) -> int:  # noqa: ANN001
        buf.grow_to(area.y + 1)
        buf.set_string(area.x, area.y, self._text, max_width=area.width)
        return 1


class TestRendererWrapper:
    def test_renders_legacy_component(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["a", "b", "c"]))

        content = _content_writes(term)
        assert len(content) == 1
        assert "a" in content[0]
        assert "b" in content[0]
        assert "c" in content[0]

    def test_renders_buffer_native_component(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(_CellsComponent("native"))

        content = _content_writes(term)
        assert len(content) == 1
        assert "native" in content[0]

    def test_overlay_composites_over_base_content(self):
        term = FakeTerminal(width=40, height=10)
        renderer = Renderer(term)  # type: ignore[arg-type]
        base = StaticComponent(["base line"] * 5)
        overlay = OverlayEntry(
            component=StaticComponent(["OVERLAY"]),
            options=OverlayOptions(width=20, height=1, anchor="top-left", margin=0),
        )

        renderer.render(base, overlays=[overlay])

        content = _content_writes(term)
        assert len(content) == 1
        assert "OVERLAY" in content[0]

    def test_no_change_writes_no_content(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["a", "b", "c"]))
        term.writes.clear()

        renderer.render(StaticComponent(["a", "b", "c"]))

        assert _content_writes(term) == []


class _DisposableComponent(Component):
    def __init__(self) -> None:
        self.disposed = False

    def render_cells(self, area: Rect, buf: Buffer) -> int:  # noqa: ARG002
        return 0

    def dispose(self) -> None:
        self.disposed = True


def test_tui_dispose_releases_components_and_resize_callbacks() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal=terminal)  # type: ignore[arg-type]
    component = _DisposableComponent()
    tui.children.append(component)

    assert len(terminal.resize_callbacks) == 2

    tui.dispose()
    tui.dispose()

    assert component.disposed
    assert terminal.resize_callbacks == []
    assert tui.children == []
