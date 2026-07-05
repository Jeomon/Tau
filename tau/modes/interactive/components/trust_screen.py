from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.text import Line, Span

if TYPE_CHECKING:
    from tau.trust.manager import TrustOption
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect
    from tau.tui.theme import LayoutTheme


class TrustScreen(Component):
    """Full-screen trust prompt shown before the normal TUI layout.

    Replaces the TUI root until the user makes a trust decision.
    Navigation: up/down arrows, Enter to confirm, Esc to cancel.
    """

    def __init__(
        self,
        cwd: str,
        options: list[TrustOption],
        on_commit: Callable[[TrustOption | None], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        self._cwd = cwd
        self._options = options
        self._selected = 0
        self._on_commit = on_commit

        if theme is None:
            from tau.tui.theme import LayoutTheme as _LT

            theme = _LT()
        self._theme = theme

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        from tau.tui.ansi_bridge import row_to_ansi
        from tau.tui.buffer import Buffer
        from tau.tui.geometry import Rect

        buf = Buffer.empty(Rect(0, 0, width, 0))
        rows = self.render_cells(Rect(0, 0, width, 0), buf)
        return [row_to_ansi(buf, row) for row in range(rows)]

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

        blank()
        blank()

        write([Span(indent), Span("Trust project folder?", t.emphasis)])
        blank()

        cwd_display = self._cwd
        if len(cwd_display) > area.width - len(indent) - 2:
            cwd_display = "…" + cwd_display[-(area.width - len(indent) - 3) :]
        write([Span(indent), Span(cwd_display, t.accent)])
        blank()

        write(
            [Span(indent), Span("This allows tau to load .py settings and resources,", t.muted)]
        )
        write(
            [
                Span(indent),
                Span(
                    "install missing project packages, and run project extensions.", t.muted
                ),
            ]
        )
        blank()
        blank()

        for i, opt in enumerate(self._options):
            is_sel = i == self._selected
            prefix = "› " if is_sel else "  "
            write([Span(indent + prefix + opt.label, t.emphasis if is_sel else t.muted)])

        blank()
        blank()

        write([Span(indent), Span("↑↓ navigate  ·  Enter select  ·  Esc cancel", t.muted)])

        return row - area.y

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        if event.key == "up":
            self._selected = (self._selected - 1) % len(self._options)
            return True

        if event.key == "down":
            self._selected = (self._selected + 1) % len(self._options)
            return True

        if event.key in ("enter", "return"):
            self._on_commit(self._options[self._selected])
            return True

        if event.key == "escape":
            self._on_commit(None)
            return True

        return False

    def invalidate(self) -> None:
        pass
