"""Mermaid fences rendered as Unicode box art.

``termaid`` lays Mermaid source out with box-drawing characters, so a diagram
shows up in every terminal rather than only the ones speaking an image
protocol. Every failure path falls back to the fenced-code rendering that was
there before, so an unsupported or oversized diagram still shows its source.
"""

from __future__ import annotations

from tau.tui.markdown import StreamingMarkdownRenderer, render_markdown
from tau.tui.mermaid import is_mermaid_language, render_diagram
from tau.tui.theme import MarkdownTheme
from tau.tui.utils import strip_ansi

FLOWCHART = "graph TD\n    A[Start] --> B[End]\n"


def _plain(md: str, width: int = 80) -> list[str]:
    return [strip_ansi(line).rstrip() for line in render_markdown(md, width, MarkdownTheme())]


def _fence(source: str, lang: str = "mermaid") -> str:
    return f"```{lang}\n{source}```\n"


def _is_art(lines: list[str]) -> bool:
    return any("┌" in line or "│" in line for line in lines)


class TestLanguageDetection:
    def test_mermaid_and_mmd_are_recognised(self):
        assert is_mermaid_language("mermaid")
        assert is_mermaid_language("mmd")

    def test_case_and_surrounding_space_are_ignored(self):
        assert is_mermaid_language("  MERMAID ")

    def test_other_languages_are_not(self):
        for lang in ("python", "", "mermaidish", "js"):
            assert not is_mermaid_language(lang), lang


class TestRenderDiagram:
    def test_a_flowchart_becomes_box_art(self):
        art = render_diagram(FLOWCHART, 80)

        assert art is not None
        assert _is_art(art)
        assert any("Start" in line for line in art)

    def test_unparseable_source_returns_none(self):
        assert render_diagram("this is not a diagram at all", 80) is None

    def test_empty_source_returns_none(self):
        assert render_diagram("", 80) is None

    def test_art_wider_than_the_terminal_returns_none(self):
        """termaid has no width parameter, so a wide graph has to fall back."""
        art = render_diagram(FLOWCHART, 80)
        assert art is not None
        widest = max(len(line) for line in art)

        assert render_diagram(FLOWCHART, widest - 1) is None
        assert render_diagram(FLOWCHART, widest) is not None

    def test_zero_width_returns_none(self):
        assert render_diagram(FLOWCHART, 0) is None

    def test_repeated_renders_are_cached(self):
        """A scrollback repaint re-renders the same diagram every frame."""
        from tau.tui.mermaid import _render_cached

        _render_cached.cache_clear()
        render_diagram(FLOWCHART, 80)
        render_diagram(FLOWCHART, 80)

        assert _render_cached.cache_info().hits >= 1


class TestMarkdownIntegration:
    def test_a_mermaid_fence_renders_as_a_diagram(self):
        lines = _plain(_fence(FLOWCHART))

        assert _is_art(lines)
        # The language label a normal fence would print is gone.
        assert "mermaid" not in "\n".join(lines)

    def test_surrounding_prose_is_preserved(self):
        lines = _plain(f"Before it.\n\n{_fence(FLOWCHART)}\nAfter it.\n")

        assert "Before it." in lines
        assert "After it." in lines
        assert _is_art(lines)

    def test_an_unrenderable_diagram_falls_back_to_source(self):
        lines = _plain(_fence("this is not a diagram at all\n"))

        assert not _is_art(lines)
        assert any("not a diagram" in line for line in lines)
        assert "mermaid" in "\n".join(lines)

    def test_a_diagram_too_wide_for_the_terminal_falls_back_to_source(self):
        wide = "graph LR\n" + "".join(
            f"  N{i}[Node number {i}] --> N{i + 1}[Node number {i + 1}]\n" for i in range(6)
        )

        lines = _plain(_fence(wide), width=60)

        assert not _is_art(lines)
        assert any("Node number" in line for line in lines)

    def test_other_code_fences_are_untouched(self):
        lines = _plain("```python\nx = 1\n```\n")

        assert not _is_art(lines)
        assert any("x = 1" in line for line in lines)
        assert "python" in "\n".join(lines)


class TestStreaming:
    """A half-written fence relayouts on every token — boxes grow and vanish."""

    def _frames_with_art(self, text: str) -> int:
        theme = MarkdownTheme()
        renderer = StreamingMarkdownRenderer()
        count = 0
        for i in range(1, len(text) + 1):
            rendered = "\n".join(strip_ansi(line) for line in renderer.render(text[:i], 80, theme))
            if "┌" in rendered:
                count += 1
        return count

    def test_an_open_fence_never_draws_a_partial_diagram(self):
        assert self._frames_with_art(_fence(FLOWCHART)) == 0

    def test_the_diagram_appears_once_the_block_is_complete(self):
        """A frozen block cannot hold an open fence, so it is safe to draw."""
        text = _fence(FLOWCHART) + "\nProse after the diagram."

        assert self._frames_with_art(text) > 0

    def test_the_finished_message_renders_the_diagram(self):
        assert _is_art(_plain(_fence(FLOWCHART)))
