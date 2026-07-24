"""Dashboard rendering — the widget above the editor and the fullscreen overlay.

Both are the same table at different sizes: a summary block (runs, baseline,
best-so-far) then one row per experiment. The widget shows the last handful of
rows; the overlay shows everything and scrolls.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import RESET, apply_style
from tau.tui.utils import rule, truncate, visible_width

from .state import (
    State,
    format_num,
    is_better,
)

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer

TITLE = "autoresearch"

#: Rows of the table the widget keeps on screen. The overlay shows everything.
WIDGET_ROWS_NARROW = 4
WIDGET_ROWS_WIDE = 6
NARROW_WIDTH = 95

_STATUS_GLYPH = {
    "keep": "✔",
    "discard": "✖",
    "crash": "✖",
    "checks_failed": "✖",
}


def _status_style(theme: Any, status: str) -> Any:
    if status == "keep":
        return theme.success
    if status in ("crash", "checks_failed"):
        return theme.error
    return theme.warning


def _spinner_frame() -> str:
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    return frames[int(time.time() * 10) % len(frames)]


def _elapsed(since: float) -> str:
    seconds = max(0, int(time.time() - since))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"


def summary_lines(state: State, theme: Any, width: int) -> list[str]:
    """Runs / baseline / best-so-far, above the table."""
    lines: list[str] = []
    muted, accent = theme.muted, theme.accent
    counts = state.counts()

    parts = [
        f"{apply_style(muted, 'Runs:')} {len(state.current())}",
        apply_style(theme.success, f"{counts['keep']} kept"),
    ]
    if counts["discard"]:
        parts.append(apply_style(theme.warning, f"{counts['discard']} discarded"))
    if counts["crash"]:
        parts.append(apply_style(theme.error, f"{counts['crash']} crashed"))
    if counts["checks_failed"]:
        parts.append(apply_style(theme.error, f"{counts['checks_failed']} checks failed"))

    confidence = state.confidence()
    if confidence is not None:
        # ≥2 likely real, 1–2 marginal, <1 inside the noise.
        style = (
            theme.success
            if confidence >= 2.0
            else theme.warning
            if confidence >= 1.0
            else theme.error
        )
        parts.append(apply_style(style, f"(conf: {confidence:.1f}×)"))
    lines.append(truncate("  " + "  ".join(parts), width))

    baseline = state.baseline()
    if baseline is not None:
        value = format_num(baseline.metric, state.metric_unit)
        lines.append(
            truncate(
                f"  {apply_style(muted, 'Baseline:')} "
                f"{apply_style(muted, f'★ {state.metric_name}: {value}')}"
                f"{apply_style(muted, f' #{state.run_number(baseline)}')}",
                width,
            )
        )

    best = state.best()
    if best is not None and baseline is not None:
        value = format_num(best.metric, state.metric_unit)
        line = (
            f"  {apply_style(muted, 'Progress:')} "
            f"{apply_style(theme.warning, f'★ {state.metric_name}: {value}')}"
            f"{apply_style(muted, f' #{state.run_number(best)}')}"
        )
        if baseline.metric:
            pct = (best.metric - baseline.metric) / baseline.metric * 100
            style = (
                theme.success
                if is_better(best.metric, baseline.metric, state.direction)
                else theme.error
            )
            line += apply_style(style, f" ({pct:+.1f}%)")
        lines.append(truncate(line, width))

    if state.running_command and state.running_since is not None:
        detail = f"{state.running_command}  ·  {_elapsed(state.running_since)}"
        lines.append(
            truncate(
                f"  {apply_style(accent, _spinner_frame())} "
                f"{apply_style(theme.warning, 'running')} {apply_style(muted, detail)}",
                width,
            )
        )
    return lines


def table_lines(state: State, theme: Any, width: int, max_rows: int) -> list[str]:
    """The results table: newest ``max_rows`` runs, oldest first.

    Column widths adapt to the terminal — the description takes whatever is
    left, and secondary metric columns are dropped from the right when there is
    not enough room for them plus a readable description.
    """
    results = state.results
    if not results:
        return [f"  {apply_style(theme.muted, 'No experiments yet.')}"]

    start = max(0, len(results) - max_rows) if max_rows > 0 else 0
    rows = results[start:]

    idx_w, commit_w, status_w = 4, 9, 10
    primary_label = f"★ {state.metric_name}"
    primary_w = max(11, min(width // 4, visible_width(primary_label) + 2))

    # Only show secondary columns that actually have values in the visible rows.
    secondary = [m for m in state.secondary if any(m.name in r.metrics for r in rows)]
    sec_widths = [
        max(
            visible_width(m.name),
            *(
                visible_width(format_num(r.metrics[m.name], m.unit))
                for r in rows
                if m.name in r.metrics
            ),
        )
        + 2
        for m in secondary
    ]
    fixed = idx_w + commit_w + primary_w + status_w + 2
    min_desc = max(12, width // 4)
    while secondary and fixed + sum(sec_widths) + min_desc > width:
        secondary.pop()
        sec_widths.pop()
    desc_w = max(min_desc, width - fixed - sum(sec_widths))

    muted = theme.muted
    header = (
        f"  {apply_style(muted, '#'.ljust(idx_w))}"
        f"{apply_style(muted, 'commit'.ljust(commit_w))}"
        f"{apply_style(theme.warning, primary_label.ljust(primary_w))}"
    )
    for m, w in zip(secondary, sec_widths, strict=True):
        header += apply_style(muted, m.name.ljust(w))
    header += f"{apply_style(muted, 'status'.ljust(status_w))}{apply_style(muted, 'description')}"

    lines = [truncate(header, width), f"  {rule(max(0, width - 4), theme.border)}"]
    if start > 0:
        lines.append(
            f"  {apply_style(muted, f'… {start} earlier run' + ('' if start == 1 else 's'))}"
        )

    baseline = state.baseline()
    for offset, result in enumerate(rows):
        number = start + offset + 1
        stale = result.segment != state.segment
        status_style = theme.muted if stale else _status_style(theme, result.status)

        value = format_num(result.metric, state.metric_unit)
        value_style = theme.muted
        if not stale and baseline is not None and result.status == "keep" and result.metric > 0:
            if is_better(result.metric, baseline.metric, state.direction):
                value_style = theme.success
            elif result.metric != baseline.metric:
                value_style = theme.error

        row = (
            f"  {apply_style(theme.muted, str(number).ljust(idx_w))}"
            f"{apply_style(theme.muted, (result.commit or '—')[:7].ljust(commit_w))}"
            f"{apply_style(value_style, value.ljust(primary_w))}"
        )
        for m, w in zip(secondary, sec_widths, strict=True):
            cell = format_num(result.metrics[m.name], m.unit) if m.name in result.metrics else "—"
            row += apply_style(theme.muted, cell.ljust(w))
        glyph = _STATUS_GLYPH.get(result.status, "·")
        row += apply_style(status_style, f"{glyph} {result.status}".ljust(status_w))
        row += apply_style(theme.muted if stale else theme.emphasis, result.description[:desc_w])
        lines.append(truncate(row, width))

    return lines


def widget_lines(state: State, theme: Any, width: int) -> list[str]:
    """Everything the above-editor widget shows."""
    from tau.modes.interactive.components.message_list import apply_render_shell

    title = f"🔬 {TITLE}" + (f": {state.name}" if state.name else "")
    body = [apply_style(theme.accent, title), ""]
    body += summary_lines(state, theme, width)
    body.append("")
    rows = WIDGET_ROWS_NARROW if width < NARROW_WIDTH else WIDGET_ROWS_WIDE
    body += table_lines(state, theme, width, rows)
    if state.max_experiments:
        budget = f"{len(state.current())}/{state.max_experiments} experiments this segment"
        body.append(f"  {apply_style(theme.muted, budget)}")
    return apply_render_shell(body, theme.message)


class DashboardOverlay(Component):
    """Scrollable view of the whole log, sized to the terminal.

    ↑/↓/j/k line, PageUp/PageDown/u/d page, g/G top/bottom, Esc/q close.

    Overlay protocol note: the TUI host calls ``render_cells`` with a
    **height-0 rect as a measuring pass** and blits ``min(returned rows,
    max_height)`` — a component that paints "into" ``area.height`` and returns
    it renders zero rows and is silently invisible. So this component sizes
    itself: it windows its content to an estimated viewport (``height_hint``)
    and returns how many rows it actually painted.
    """

    def __init__(
        self,
        state: State,
        theme: Any,
        on_close: Any,
        height_hint: Callable[[], int] | None = None,
    ) -> None:
        self._state = state
        self._theme = theme
        self._on_close = on_close
        self._height_hint = height_hint
        self._offset = 0
        self._last_height = 20

    def _lines(self, width: int) -> list[str]:
        state, theme = self._state, self._theme
        title = f"🔬 {TITLE}" + (f": {state.name}" if state.name else "")
        lines = [f"  {apply_style(theme.accent, title)}", ""]
        lines += summary_lines(state, theme, width)
        lines.append("")
        lines += table_lines(state, theme, width, max_rows=0)  # everything
        return lines

    def _viewport_rows(self) -> int:
        """Body rows to show — conservative vs the host's 90% max_height clamp,
        so the footer always survives the ``min`` and stays visible."""
        term_h = 0
        if callable(self._height_hint):
            try:
                term_h = int(self._height_hint() or 0)
            except (TypeError, ValueError):
                term_h = 0
        if term_h <= 0:
            term_h = 40
        return max(6, int(term_h * 0.85) - 2)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_into

        theme = self._theme
        lines = self._lines(area.width)
        body_rows = min(len(lines), self._viewport_rows())
        self._last_height = body_rows
        self._offset = max(0, min(self._offset, max(0, len(lines) - body_rows)))
        visible = lines[self._offset : self._offset + body_rows]

        footer_bits = ["↑/↓ scroll  ·  PgUp/PgDn page  ·  g/G top/bottom  ·  Esc close"]
        hidden = len(lines) - body_rows
        if hidden > 0:
            footer_bits.append(f"line {self._offset + 1}–{self._offset + body_rows}/{len(lines)}")
        footer = "  " + apply_style(theme.muted, "  ·  ".join(footer_bits)) + RESET

        rows = 0
        buf.grow_to(area.y + len(visible) + 1)
        for line in visible:
            parse_ansi_into(buf, area.x, area.y + rows, line, area.width)
            rows += 1
        parse_ansi_into(buf, area.x, area.y + rows, footer, area.width)
        rows += 1
        return rows

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        page = max(1, self._last_height - 1)
        match event.key:
            case "up" | "k":
                self._offset = max(0, self._offset - 1)
            case "down" | "j":
                self._offset += 1
            case "page_up" | "u":
                self._offset = max(0, self._offset - page)
            case "page_down" | "d":
                self._offset += page
            case "g" | "home":
                self._offset = 0
            case "G" | "end":
                self._offset = 10**6  # clamped on the next render
            case "escape" | "q":
                self._on_close()
            case _:
                return False
        return True

    def invalidate(self) -> None:
        pass
