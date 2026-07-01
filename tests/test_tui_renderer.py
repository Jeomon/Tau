"""Tests for tau/tui/tui.py — Renderer differential-render invariants.

These exercise the claim that Renderer.render() only rewrites the contiguous
span of lines that actually changed between frames, leaving everything above
first_changed and below last_changed untouched.
"""

from __future__ import annotations

from tau.tui.component import StaticComponent
from tau.tui.tui import Renderer


class FakeTerminal:
    """Minimal stand-in for tau.tui.terminal.Terminal, capturing writes."""

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

    def on_resize(self, callback: object) -> object:  # noqa: ARG002
        return lambda: None


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
