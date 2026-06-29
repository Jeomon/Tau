from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 8


@dataclass
class OAuthProviderItem:
    """A single row in OAuthSelector."""

    id: str
    name: str
    status: str | None = None  # e.g. "configured", "env: ANTHROPIC_API_KEY"


class OAuthSelector(Component):
    """Provider picker for /login and /logout."""

    def __init__(
        self,
        mode: Literal["login", "logout"],
        providers: list[OAuthProviderItem],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._mode = mode
        self._providers = providers
        self._selected = 0
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._theme = theme or LT()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = t.border("─" * width)
        lines: list[str] = []

        title = "Configure provider:" if self._mode == "login" else "Logout from provider:"
        lines.append("  " + t.emphasis(title))
        lines.append(divider)

        if not self._providers:
            msg = (
                "No providers logged in. Use /login first."
                if self._mode == "logout"
                else "No providers available"
            )
            lines.append("  " + t.muted(msg))
        else:
            start = max(
                0,
                min(
                    self._selected - _VISIBLE_ROWS // 2,
                    max(0, len(self._providers) - _VISIBLE_ROWS),
                ),
            )
            end = min(start + _VISIBLE_ROWS, len(self._providers))

            if start > 0:
                lines.append("  " + t.muted(f"↑ {start} more"))

            for i in range(start, end):
                p = self._providers[i]
                if p.status and p.status.startswith("✓"):
                    status_part = f"  {t.success('✓')}{t.muted(p.status[1:])}"
                elif p.status:
                    status_part = f"  {t.muted(p.status)}"
                else:
                    status_part = ""

                if i == self._selected:
                    lines.append(f"  {t.emphasis(f'→ {p.name}')}{status_part}")
                else:
                    lines.append(f"    {p.name}{status_part}")

            remaining = len(self._providers) - end
            if remaining > 0:
                lines.append("  " + t.muted(f"↓ {remaining} more"))

        lines.append(divider)
        lines.append("  " + t.muted("↑↓ navigate  enter select  esc cancel"))

        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        kb = get_keybindings()

        if kb.matches(event, "tui.select.up"):
            if self._providers:
                self._selected = max(0, self._selected - 1)
            return True

        if kb.matches(event, "tui.select.down"):
            if self._providers:
                self._selected = min(len(self._providers) - 1, self._selected + 1)
            return True

        if kb.matches(event, "tui.select.confirm"):
            if self._providers:
                self._on_select(self._providers[self._selected].id)
            return True

        if kb.matches(event, "tui.select.dismiss"):
            self._on_cancel()
            return True

        return False

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
