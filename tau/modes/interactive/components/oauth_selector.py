from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.components.simple_picker import DEFAULT_HINT, PickerRow, render_picker_cells
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.style import Style, apply_style
from tau.tui.text import Span
from tau.tui.widgets.list import ListState

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
        self._list_state = ListState()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
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

        return render_picker_cells(
            buf,
            area,
            header=["  " + apply_style(t.emphasis, title)],
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=_VISIBLE_ROWS,
            border_style=t.border,
            muted_style=t.muted,
            accent_style=t.accent,
            emphasis_style=t.emphasis,
            hint=DEFAULT_HINT,
            empty_text=empty_text,
        )

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
