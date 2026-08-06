from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tau.modes.interactive.components.selector_base import KeyboundSelector
from tau.tui.components.simple_picker import PickerRow, render_picker_lines
from tau.tui.style import Style, apply_style
from tau.tui.text import Span

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 8


@dataclass
class OAuthProviderItem:
    """A single row in OAuthSelector."""

    id: str
    name: str
    status: str | None = None  # e.g. "configured", "env: ANTHROPIC_API_KEY"


class OAuthSelector(KeyboundSelector):
    """Provider picker for /login and /logout."""

    page_size = _VISIBLE_ROWS

    def __init__(
        self,
        mode: Literal["login", "logout"],
        providers: list[OAuthProviderItem],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        super().__init__(on_select, on_cancel, theme)
        self._mode = mode
        self._providers = providers

    def _items(self) -> list:
        return self._providers

    def _confirm_value(self) -> str:
        return self._providers[self._selected].id

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        t = self._theme
        title = "Configure provider:" if self._mode == "login" else "Logout from provider:"

        rows = []
        for p in self._providers:
            spans: list[Span] = []
            if p.status and p.status.startswith("✓"):
                spans = [
                    Span("  ", Style()),
                    Span("✓", t.success),
                    Span(p.status[1:], t.muted),
                ]
            elif p.status:
                spans = [Span("  ", Style()), Span(p.status, t.muted)]
            rows.append(PickerRow(p.name, spans))

        if not self._providers:
            empty_text = (
                "No providers logged in. Use /login first."
                if self._mode == "logout"
                else "No providers available"
            )
        else:
            empty_text = ""

        return render_picker_lines(
            width,
            header=["  " + apply_style(t.emphasis, title)],
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=_VISIBLE_ROWS,
            theme=t,
            empty_text=empty_text,
        )
