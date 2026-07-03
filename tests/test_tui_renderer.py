"""Tests for tau/tui/tui.py — Renderer differential-render invariants.

These exercise the claim that Renderer.render() only rewrites the contiguous
span of lines that actually changed between frames, leaving everything above
first_changed and below last_changed untouched.
"""

from __future__ import annotations

from tau.tui import tui as tui_module
from tau.tui.component import Component, StaticComponent
from tau.tui.tui import TUI, Renderer


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
    """Writes from render()'s main buffer, excluding the trailing cursor-hide write_flush."""
    return [w for w in term.writes if w != "\x1b[?25l"]


class TestRendererDifferentialUpdate:
    def test_first_render_draws_everything(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["a", "b", "c"]))

        content = _content_writes(term)
        assert len(content) == 1
        assert "a" in content[0]
        assert "b" in content[0]
        assert "c" in content[0]

    def test_no_change_writes_no_content(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        lines = ["a", "b", "c"]
        renderer.render(StaticComponent(lines))
        term.writes.clear()

        renderer.render(StaticComponent(list(lines)))

        assert _content_writes(term) == []

    def test_appended_line_only_redraws_new_line(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["a", "b", "c"]))
        term.writes.clear()

        renderer.render(StaticComponent(["a", "b", "c", "d"]))

        content = _content_writes(term)
        assert len(content) == 1
        assert "d" in content[0]
        # Unchanged lines above the appended one must not be rewritten.
        assert "a" not in content[0]
        assert "b" not in content[0]
        assert "c" not in content[0]

    def test_single_middle_line_change_only_redraws_that_line(self):
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["a", "b", "c"]))
        term.writes.clear()

        renderer.render(StaticComponent(["a", "X", "c"]))

        content = _content_writes(term)
        assert len(content) == 1
        assert "X" in content[0]
        assert "a" not in content[0]
        assert "c" not in content[0]

    def test_appended_suffix_only_rewrites_from_divergence_point(self):
        """Ratatui-style cell diffing: a changed line reuses its unchanged
        leading run instead of clearing and rewriting the whole line."""
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["hello wor"]))
        term.writes.clear()

        renderer.render(StaticComponent(["hello world"]))

        content = _content_writes(term)
        assert len(content) == 1
        # Only the diverging suffix is written, not the shared "hello wor" prefix.
        assert "ld" in content[0]
        assert "hello wor" not in content[0]
        # Cursor is repositioned to the divergence column with an absolute
        # column move, and only the tail is cleared — not the whole line.
        assert "G" in content[0]
        assert "\x1b[0K" in content[0]
        assert "\x1b[2K" not in content[0]

    def test_prefix_diff_preserves_ansi_styling_state(self):
        """A style code shared by the unchanged prefix must not be re-sent,
        since the terminal already has it active from the previous frame."""
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["\x1b[31mred a"]))
        term.writes.clear()

        renderer.render(StaticComponent(["\x1b[31mred b"]))

        content = _content_writes(term)
        assert len(content) == 1
        assert "\x1b[31m" not in content[0]
        assert "b" in content[0]
        assert "red" not in content[0]

    def test_sparse_changes_redraw_full_span_including_unchanged_lines(self):
        """Documents the known limitation: two far-apart changed lines cause the
        whole span between them to be repainted, not just the two changed lines.
        """
        term = FakeTerminal()
        renderer = Renderer(term)  # type: ignore[arg-type]
        renderer.render(StaticComponent(["a", "b", "c", "d", "e"]))
        term.writes.clear()

        renderer.render(StaticComponent(["a", "Z", "c", "d", "Y"]))

        content = _content_writes(term)
        assert len(content) == 1
        # Both changed lines are present...
        assert "Z" in content[0]
        assert "Y" in content[0]
        # ...and so are the unchanged lines within the span, since the whole
        # [first_changed, last_changed] range is repainted contiguously.
        assert "c" in content[0]
        assert "d" in content[0]
        # Lines outside the span (before first_changed) are never touched.
        assert "a" not in content[0]

    def test_stable_height_offscreen_change_does_not_redraw(self):
        term = FakeTerminal(width=20, height=5)
        renderer = Renderer(term)  # type: ignore[arg-type]
        lines = [f"line {i}" for i in range(20)]
        renderer.render(StaticComponent(lines))
        term.writes.clear()

        changed = list(lines)
        changed[0] = "offscreen"
        renderer.render(StaticComponent(changed))

        assert _content_writes(term) == []
        assert renderer._prev_lines[0].strip() == "offscreen"

    def test_stable_height_change_clamps_redraw_to_viewport(self):
        term = FakeTerminal(width=20, height=5)
        renderer = Renderer(term)  # type: ignore[arg-type]
        lines = [f"line {i}" for i in range(20)]
        renderer.render(StaticComponent(lines))
        term.writes.clear()

        changed = list(lines)
        changed[0] = "offscreen"
        changed[19] = "visible"
        renderer.render(StaticComponent(changed))

        content = _content_writes(term)
        assert len(content) == 1
        assert "visible" in content[0]
        assert "line 15" in content[0]
        assert "offscreen" not in content[0]
        assert "\x1b[2J" not in content[0]

    def test_unchanged_lines_reuse_width_calculation(self, monkeypatch):
        term = FakeTerminal(width=100, height=24)
        renderer = Renderer(term)  # type: ignore[arg-type]
        lines = [f"\x1b[2mline {index}\x1b[0m" for index in range(10_000)]
        renderer.render(StaticComponent(lines))

        calls = 0
        original = tui_module.visible_width

        def counted(text: str) -> int:
            nonlocal calls
            calls += 1
            return original(text)

        monkeypatch.setattr(tui_module, "visible_width", counted)
        changed = list(lines)
        changed[-1] = "\x1b[2mchanged\x1b[0m"

        renderer.render(StaticComponent(changed))

        assert calls == 1


class _DisposableComponent(Component):
    def __init__(self) -> None:
        self.disposed = False

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        return []

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
