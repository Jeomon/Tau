"""Monthly: a month calendar grid.

Per-date styling is pluggable via ``DateStyler`` (a structural ``Protocol``,
matching the rendering protocol) rather than the single ``highlighted_days: set[int]``
this used to carry — ``CalendarEventStore`` is the built-in dict-backed
implementation.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style

_WEEKDAY_HEADER = "Mo Tu We Th Fr Sa Su"


@runtime_checkable
class DateStyler(Protocol):
    def get_style(self, d: date) -> Style | None: ...  # None = no override for this date


@dataclass(slots=True)
class CalendarEventStore:
    """A dict-backed ``DateStyler`` — the common case of "style these specific dates"."""

    events: dict[date, Style] = field(default_factory=dict)

    def add(self, d: date, style: Style) -> None:
        self.events[d] = style

    def get_style(self, d: date) -> Style | None:
        return self.events.get(d)


@dataclass(slots=True)
class Monthly:
    year: int
    month: int
    style: Style = field(default_factory=Style)
    header_style: Style = field(default_factory=lambda: Style().bold())
    surrounding_style: Style = field(default_factory=lambda: Style().dim())
    show_surrounding: bool = False
    styler: DateStyler | None = None

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return
        buf.set_string(area.left, area.top, _WEEKDAY_HEADER, self.header_style, area.width)

        weeks = _calendar.Calendar(firstweekday=0).monthdatescalendar(self.year, self.month)
        for row, week in enumerate(weeks):
            y = area.top + 1 + row
            if y >= area.bottom:
                break
            for col, d in enumerate(week):
                in_month = d.month == self.month
                if not in_month and not self.show_surrounding:
                    continue
                x = area.left + col * 3
                if x + 2 > area.right:
                    continue
                base = self.style if in_month else self.surrounding_style
                override = self.styler.get_style(d) if self.styler is not None else None
                style = base.patch(override) if override is not None else base
                buf.set_string(x, y, f"{d.day:>2}", style)
