from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.text import Line, Span

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect
    from tau.tui.theme import LayoutTheme


@dataclass(frozen=True)
class FirstRunResult:
    """Choices made on the first-run setup screen."""

    theme: str
    share_telemetry: bool


_TELEMETRY_OPTIONS: list[tuple[bool, str]] = [
    (True, "Share anonymous usage data"),
    (False, "Don't share"),
]


class FirstRunScreen(Component):
    """One-time welcome screen shown when no settings file exists yet.

    Two steps — theme choice (with live preview) and telemetry opt-in —
    using the same full-screen root-swap pattern as TrustScreen.
    Navigation: up/down arrows, Enter to continue/finish, Esc to skip.
    Skipping commits ``None`` so nothing is persisted and the screen is
    offered again on the next launch.
    """

    def __init__(
        self,
        theme_options: list[tuple[str, str]],
        on_preview: Callable[[str], None],
        on_commit: Callable[[FirstRunResult | None], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        self._theme_options = theme_options
        self._on_preview = on_preview
        self._on_commit = on_commit
        self._step: str = "theme"
        self._theme_selected = 0
        self._telemetry_selected = 0

        if theme is None:
            from tau.tui.theme import LayoutTheme as _LT

            theme = _LT()
        self._theme = theme

    def set_theme(self, theme: LayoutTheme) -> None:
        """Recolor the screen (called when a theme preview is applied)."""
        self._theme = theme

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        indent = "  "
        row = area.y

        def write(spans: list[Span]) -> None:
            nonlocal row
            buf.grow_to(row + 1)
            buf.set_line(area.x, row, Line(spans), area.width)
            row += 1

        def blank() -> None:
            write([])

        def options(labels: list[str], selected: int) -> None:
            for i, label in enumerate(labels):
                is_sel = i == selected
                prefix = "› " if is_sel else "  "
                write([Span(indent + prefix + label, t.emphasis if is_sel else t.muted)])

        blank()
        blank()

        write([Span(indent), Span("Welcome to τ, let's set things up.", t.emphasis)])
        blank()

        if self._step == "theme":
            write([Span(indent), Span("Pick a theme — previewed live as you move.", t.accent)])
            write([Span(indent), Span("More themes are available later via /theme.", t.muted)])
            blank()
            options([label for _, label in self._theme_options], self._theme_selected)
            blank()

            # Sample conversation rendered with the styles themes actually
            # differ on (labels, markdown, tools, diffs) — the shared role
            # colors used elsewhere on this screen are theme-invariant.
            m = t.message
            write([Span(indent), Span("─" * 44, t.divider)])
            write([Span(indent), Span("You: ", m.you_label), Span("fix the failing parser test")])
            write(
                [
                    Span(indent),
                    Span("τ ", m.assistant_label),
                    Span("The bug is in ", t.muted),
                    Span("parse_args()", m.markdown.code_inline),
                    Span(" — patching now.", t.muted),
                ]
            )
            write([Span(indent), Span("→ Edit(parser.py)", m.tool_arrow)])
            write([Span(indent), Span("  + expected = args.strip()", m.diff_added)])
            write([Span(indent), Span("  - expected = args", m.diff_removed)])
            write(
                [
                    Span(indent),
                    Span("Error: ", m.error_label),
                    Span("example failure message", t.muted),
                ]
            )
            write([Span(indent), Span("─" * 44, t.divider)])
        else:
            write([Span(indent), Span("Share anonymous usage data?", t.accent)])
            write(
                [
                    Span(indent),
                    Span("tau sends an anonymous, version-only ping on install and", t.muted),
                ]
            )
            write(
                [
                    Span(indent),
                    Span("update — no prompts, code, or personal data. Change anytime", t.muted),
                ]
            )
            write([Span(indent), Span('via "telemetry" in ~/.tau/settings.json.', t.muted)])
            blank()
            options([label for _, label in _TELEMETRY_OPTIONS], self._telemetry_selected)

        blank()
        blank()

        confirm = "Enter continue" if self._step == "theme" else "Enter finish"
        write([Span(indent), Span(f"↑↓ navigate  ·  {confirm}  ·  Esc skip setup", t.muted)])

        return row - area.y

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        if event.key in ("up", "down"):
            delta = -1 if event.key == "up" else 1
            if self._step == "theme":
                self._theme_selected = (self._theme_selected + delta) % len(self._theme_options)
                self._on_preview(self._theme_options[self._theme_selected][0])
            else:
                self._telemetry_selected = (self._telemetry_selected + delta) % len(
                    _TELEMETRY_OPTIONS
                )
            return True

        if event.key in ("enter", "return"):
            if self._step == "theme":
                self._step = "telemetry"
            else:
                self._on_commit(
                    FirstRunResult(
                        theme=self._theme_options[self._theme_selected][0],
                        share_telemetry=_TELEMETRY_OPTIONS[self._telemetry_selected][0],
                    )
                )
            return True

        if event.key == "escape":
            self._on_commit(None)
            return True

        return False

    def invalidate(self) -> None:
        pass
