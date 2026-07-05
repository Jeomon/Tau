from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import fuzzy_filter
from tau.tui.widgets.list import List, ListItem, ListState

if TYPE_CHECKING:
    from tau.tui.theme import SelectListTheme

T = TypeVar("T")

_log = logging.getLogger(__name__)


@runtime_checkable
class SelectorComponent(Protocol):
    """Minimum interface an ``InlineSelector.selector`` must satisfy.

    ``InlineSelector.selector`` is kept as ``Any`` to avoid a circular
    import (the concrete selectors live in ``tau.modes.interactive``, which
    imports this module) — this Protocol lets ``InlineSelector`` still
    isinstance-check it at construction time instead of only discovering a
    missing ``render_cells`` mid-render.
    """

    def render_cells(self, area: Rect, buf: Buffer) -> int: ...


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

    Rendering is built on the ratatui-style ``List``/``ListState`` widgets
    (``tau/tui/widgets/list.py``): selection/scroll-offset state lives in a
    real ``ListState``, and each row's two-column label/description layout
    is two ``Span``s in a ``ListItem``'s ``Line``. The scroll "N more"
    indicators aren't part of ``List`` itself (ratatui's List has no such
    concept) — they're rendered as their own rows above/below it, same as
    before.

    Usage::

        lst = SelectList(items, max_visible=5, theme=theme.select)
        lst.set_query(current_input)
        lst.on_confirm(lambda item: ...)
        rows = lst.render_cells(area, buf)
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
        self._state = ListState(selected=0)
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
        return self._filtered[self._state.selected or 0]

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

    def set_selected(self, index: int) -> None:
        """Jump directly to ``index`` (clamped) — e.g. to seed an initial selection."""
        if self._filtered:
            self._state.select(max(0, min(index, len(self._filtered) - 1)))
            self._clamp_scroll()

    def move_up(self) -> None:
        if self._filtered:
            selected = self._state.selected or 0
            self._state.select((selected - 1) % len(self._filtered))
            self._clamp_scroll()

    def move_down(self) -> None:
        if self._filtered:
            selected = self._state.selected or 0
            self._state.select((selected + 1) % len(self._filtered))
            self._clamp_scroll()

    def page_up(self) -> None:
        if self._filtered:
            selected = self._state.selected or 0
            self._state.select(max(0, selected - self._max_visible))
            self._clamp_scroll()

    def page_down(self) -> None:
        if self._filtered:
            selected = self._state.selected or 0
            self._state.select(min(len(self._filtered) - 1, selected + self._max_visible))
            self._clamp_scroll()

    def move_top(self) -> None:
        if self._filtered:
            self._state.select(0)
            self._clamp_scroll()

    def move_bottom(self) -> None:
        if self._filtered:
            self._state.select(len(self._filtered) - 1)
            self._clamp_scroll()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        items = self._filtered

        if not items:
            buf.grow_to(area.y + 1)
            buf.set_string(area.x, area.y, "  no matches", t.empty)
            return 1

        count = len(items)
        visible = min(self._max_visible, count)

        # Keep scroll window so selected stays in view (mirrors List.render's
        # own ensure_visible call, run early so the label-width pass below
        # sees the same visible slice List will actually draw).
        self._clamp_scroll()
        start = self._state.offset
        selected = self._state.selected if self._state.selected is not None else -1

        # Label column width: widest label in visible slice (min 8, max ~40% of width)
        label_w = max(
            8,
            min(
                max(len(it.label) for it in items[start : start + visible]),
                area.width // 2,
            ),
        )
        desc_w = max(0, area.width - label_w - 3)  # 3 = "  " indent + " " gap

        y = area.y
        rows = 0

        if start > 0:
            buf.grow_to(y + 1)
            buf.set_string(area.x, y, f"  ↑ {start} more", t.indicator)
            y += 1
            rows += 1

        list_items: list[ListItem] = []
        for i, item in enumerate(items):
            is_sel = i == selected
            label = item.label[:label_w].ljust(label_w)
            desc = item.description[:desc_w] if desc_w > 0 else ""
            label_style = t.selected_label if is_sel else t.normal_label
            desc_style = t.selected_desc if is_sel else t.normal_desc
            line = Line([Span(label, label_style), Span(" ", Style()), Span(desc, desc_style)])
            list_items.append(ListItem(line))

        list_area = Rect(area.x, y, area.width, visible)
        buf.grow_to(y + visible)
        widget = List(
            items=list_items,
            highlight_symbol="  ",
            highlight_style=t.selected_bg if t.selected_bg is not None else Style(),
        )
        widget.render(list_area, buf, self._state)
        y += visible
        rows += visible

        remaining = count - (start + visible)
        if remaining > 0:
            buf.grow_to(y + 1)
            buf.set_string(area.x, y, f"  ↓ {remaining} more", t.indicator)
            rows += 1

        return rows

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

        if kb.matches(event, "tui.select.page_up"):
            self.page_up()
            return True

        if kb.matches(event, "tui.select.page_down"):
            self.page_down()
            return True

        if kb.matches(event, "tui.select.top"):
            self.move_top()
            return True

        if kb.matches(event, "tui.select.bottom"):
            self.move_bottom()
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
            selected = self._state.selected or 0
            self._state.select(min(selected, len(self._filtered) - 1))
        else:
            self._state.select(0)
        self._state.offset = 0

    def _clamp_scroll(self) -> None:
        count = len(self._filtered)
        visible = min(self._max_visible, count)
        self._state.ensure_visible(count, visible)


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

    def __post_init__(self) -> None:
        if not isinstance(self.selector, SelectorComponent):
            _log.warning(
                "InlineSelector(kind=%r).selector (%r) does not satisfy "
                "SelectorComponent (missing render_cells); it will crash the "
                "next render instead of here. See tau.tui.components.select_list.",
                self.kind,
                type(self.selector).__name__,
            )

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def nav(self, direction: int) -> None:
        self.selector.move_up() if direction < 0 else self.selector.move_down()

    def selected_value(self) -> T | None:
        item = self.selector.selected_item
        return item.value if item is not None else None
