from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import apply_style

if TYPE_CHECKING:
    from tau.trust.manager import TrustOption
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
        t = self._theme
        lines: list[str] = []
        indent = "  "

        lines.append("")
        lines.append("")

        lines.append(indent + apply_style(t.emphasis, "Trust project folder?"))
        lines.append("")

        cwd_display = self._cwd
        if len(cwd_display) > width - len(indent) - 2:
            cwd_display = "…" + cwd_display[-(width - len(indent) - 3) :]
        lines.append(indent + apply_style(t.accent, cwd_display))
        lines.append("")

        line1 = apply_style(t.muted, "This allows tau to load .py settings and resources,")
        line2 = apply_style(
            t.muted, "install missing project packages, and run project extensions."
        )
        lines.append(indent + line1)
        lines.append(indent + line2)
        lines.append("")
        lines.append("")

        for i, opt in enumerate(self._options):
            is_sel = i == self._selected
            prefix = "› " if is_sel else "  "
            row = indent + prefix + opt.label
            lines.append(apply_style(t.emphasis if is_sel else t.muted, row))

        lines.append("")
        lines.append("")

        hint = apply_style(t.muted, "↑↓ navigate  ·  Enter select  ·  Esc cancel")
        lines.append(indent + hint)

        return lines

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
