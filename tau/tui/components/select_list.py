from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.utils import fuzzy_filter, visible_width

if TYPE_CHECKING:
    from tau.tui.theme import SelectListTheme

T = TypeVar("T")


@dataclass
class SelectItem[T]:
    """A single row in a SelectList."""

    label: str
    description: str = ""
    value: T | None = None  # type: ignore[assignment]


class SelectList[T](Component):
    """
    Filterable, scrollable list of SelectItem rows.

    - Fuzzy-filters items as `query` changes.
    - Arrow keys / ctrl+p / ctrl+n navigate selection.
    - Enter / Tab fires the on_confirm callback.
    - Escape fires on_dismiss.
    - Shows a scroll indicator when items overflow the viewport.

    Usage::

        lst = SelectList(items, max_visible=5, theme=theme.select)
        lst.set_query(current_input)
        lst.on_confirm(lambda item: ...)
        lines = lst.render(width)
    """

    def __init__(
        self,
        items: list[SelectItem[T]] | None = None,
        max_visible: int = 5,
        theme: SelectListTheme | None = None,
    ) -> None:
        self._all_items: list[SelectItem[T]] = items or []
        self._filtered: list[SelectItem[T]] = list(self._all_items)
        self._max_visible = max(1, max_visible)
        self._selected = 0
        self._scroll_offset = 0
        self._query = ""
        self._on_confirm: Callable[[SelectItem[T]], None] | None = None
        self._on_dismiss: Callable[[], None] | None = None

        from tau.tui.theme import SelectListTheme as _ST

        self._theme = theme or _ST()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return bool(self._filtered)

    @property
    def selected_item(self) -> SelectItem[T] | None:
        if not self._filtered:
            return None
        return self._filtered[self._selected]

    @property
    def line_count(self) -> int:
        return min(self._max_visible, len(self._filtered))

    def set_items(self, items: list[SelectItem[T]]) -> None:
        self._all_items = items
        self._apply_filter()

    def set_query(self, query: str) -> None:
        if query == self._query:
            return
        self._query = query
        self._apply_filter()

    def set_theme(self, theme: SelectListTheme) -> None:
        self._theme = theme

    def on_confirm(self, cb: Callable[[SelectItem[T]], None]) -> None:
        self._on_confirm = cb

    def on_dismiss(self, cb: Callable[[], None]) -> None:
        self._on_dismiss = cb

    def move_up(self) -> None:
        if self._filtered:
            self._selected = (self._selected - 1) % len(self._filtered)
            self._clamp_scroll()

    def move_down(self) -> None:
        if self._filtered:
            self._selected = (self._selected + 1) % len(self._filtered)
            self._clamp_scroll()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        t = self._theme
        items = self._filtered

        if not items:
            return [t.empty("  no matches")]

        count = len(items)
        visible = min(self._max_visible, count)

        # Keep scroll window so selected stays in view
        self._clamp_scroll()
        start = self._scroll_offset

        # Label column width: widest label in visible slice (min 8, max ~40% of width)
        label_w = max(
            8,
            min(
                max(len(it.label) for it in items[start : start + visible]),
                width // 2,
            ),
        )
        desc_w = max(0, width - label_w - 3)  # 3 = "  " indent + " " gap

        lines: list[str] = []

        # Scroll-up indicator
        if start > 0:
            lines.append(t.indicator(f"  ↑ {start} more"))

        for i in range(start, start + visible):
            item = items[i]
            is_sel = i == self._selected

            label = item.label[:label_w].ljust(label_w)
            desc = item.description[:desc_w] if desc_w > 0 else ""

            if is_sel:
                row = "  " + t.selected_label(label) + " " + t.selected_desc(desc)
                if t.selected_bg:
                    # Fill to full width and apply background
                    vw = visible_width(row)
                    fill = max(0, width - vw)
                    row = t.selected_bg(row + " " * fill)
            else:
                row = "  " + t.normal_label(label) + " " + t.normal_desc(desc)

            lines.append(row)

        # Scroll-down indicator
        remaining = count - (start + visible)
        if remaining > 0:
            lines.append(t.indicator(f"  ↓ {remaining} more"))

        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        kb = get_keybindings()

        if kb.matches(event, "tui.select.up"):
            self.move_up()
            return True

        if kb.matches(event, "tui.select.down"):
            self.move_down()
            return True

        if kb.matches(event, "tui.select.confirm"):
            item = self.selected_item
            if item is not None and self._on_confirm is not None:
                self._on_confirm(item)
            return True

        if kb.matches(event, "tui.select.dismiss"):
            if self._on_dismiss is not None:
                self._on_dismiss()
            return True

        return False

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _apply_filter(self) -> None:
        if not self._query:
            self._filtered = list(self._all_items)
        else:
            self._filtered = fuzzy_filter(
                self._all_items,
                self._query,
                lambda item: item.label + " " + item.description,
            )
        # Clamp selection to new list length
        if self._filtered:
            self._selected = min(self._selected, len(self._filtered) - 1)
        else:
            self._selected = 0
        self._scroll_offset = 0

    def _clamp_scroll(self) -> None:
        count = len(self._filtered)
        visible = min(self._max_visible, count)
        # selected must be in [scroll_offset, scroll_offset + visible)
        if self._selected < self._scroll_offset:
            self._scroll_offset = self._selected
        elif self._selected >= self._scroll_offset + visible:
            self._scroll_offset = self._selected - visible + 1
        # ensure scroll_offset valid
        self._scroll_offset = max(0, min(self._scroll_offset, max(0, count - visible)))


# ── InlineSelector ────────────────────────────────────────────────────────────


@dataclass
class InlineSelector[T]:
    """
    Generic wrapper for an inline selector modal.

    Handles the open/nav/commit/cancel lifecycle for model, resume, tree,
    and settings selectors. Theme and effort selectors manage their own
    callbacks via Component.handle_input and do not use on_commit/on_cancel.
    """

    kind: str  # "model" | "theme" | "effort" | "resume" | "tree" | "settings"
    selector: Any  # inner selector — kept as Any to avoid circular import
    on_commit: Callable[[T], None] | None = None
    on_cancel: Callable[[], None] | None = None

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def nav(self, direction: int) -> None:
        self.selector.move_up() if direction < 0 else self.selector.move_down()

    def selected_value(self) -> T | None:
        item = self.selector.selected_item
        return item.value if item is not None else None

    # -------------------------------------------------------------------------
    # Render
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        return self.selector.render(width)
