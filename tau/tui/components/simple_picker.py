"""Shared "title + divider + scrollable list + divider + hint" picker layout.

Consolidates what used to be ~9 near-identical hand-rolled implementations
(ExtensionSelector, ThemeSelector, ThinkingSelector, VoiceSelector,
OAuthSelector, CommandPalette, ...) onto the grid-based ``List``/
``ListState`` widgets (``tau/tui/widgets/list.py``) for the scroll-window
slicing — ``List.render``'s ``state.offset``/``area.height`` logic replaces
each selector's own ``range(start, start + visible)`` loop.

The scroll *offset itself* is still computed by the exact original
centering formula (``selected - visible // 2``, clamped), not
``ListState.ensure_visible`` — that method implements a different,
minimal/"keep visible" scroll (only moves the window when the selection
would otherwise fall outside it), which is what ``SelectList`` already
needed and correctly uses, but is not byte-compatible with these
selectors' centering behavior. The computed offset is seeded directly onto
``state`` before ``List.render()`` runs; since a centered offset always
also satisfies the weaker "keep visible" constraint, ``List.render()``'s
own internal ``ensure_visible`` call is a no-op on top of it.

Row styling stays manual (built as pre-styled ``Span``s) rather than using
``List.highlight_style``: these selectors color the "> " marker itself
(not a full-row background), and ``List.render`` unconditionally re-patches
``highlight_style`` across every cell of a selected row — which would
clobber a label's own emphasis-style fg the moment ``highlight_style`` sets
one. Keeping ``highlight_style=Style()`` (a true no-op patch) and
``highlight_symbol=""`` sidesteps that; the "> " marker and label/suffix
colors are just spans in the row's own Line, exactly as before.

Known limitation: a single ``ListItem`` is always one row (the List
has no concept of a wrapped multi-row item). The legacy ``render(width)``
path let an overlong row (long label + description, e.g.
ThinkingSelector's) wrap onto extra rows via the generic wrap-aware
Component bridge; here it's clipped to ``area.width`` instead. Verified
byte-identical against the original at realistic widths (60+ columns,
where these selectors actually render); only affects terminals narrower
than ~50 columns for description-heavy rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style, apply_style
from tau.tui.text import Line, Span
from tau.tui.widgets.list import List, ListItem, ListState


@dataclass
class PickerRow:
    """One row: a label plus optional pre-styled trailing spans (checkmark,
    description, status text, ...). Spans are independent of selection
    state — the label's own style is chosen by render_picker_cells based on
    whether this row is selected."""

    label: str
    suffix_spans: list[Span] = field(default_factory=list)


def render_picker_cells(
    buf: Buffer,
    area: Rect,
    *,
    header: list[str],
    rows: list[PickerRow],
    selected: int,
    state: ListState,
    max_visible: int,
    border_style: Style,
    muted_style: Style,
    accent_style: Style,
    emphasis_style: Style,
    hint: str,
    empty_text: str = "No options available",
) -> int:
    """Render the shared picker layout into buf. Returns rows written.

    ``state`` is owned by the caller and persisted across renders (same
    ``ListState`` instance each call) so scroll position carries over.
    """
    from tau.tui.ansi_bridge import parse_ansi_into
    from tau.tui.utils import visible_width, wrap

    y = area.y

    def write(line: str) -> None:
        nonlocal y
        # Match Component's default render_cells bridge: a line that
        # overflows area.width wraps onto more rows rather than being cut
        # off by parse_ansi_into's max_width bound.
        for wl in wrap(line, area.width) if visible_width(line) > area.width else [line]:
            buf.grow_to(y + 1)
            parse_ansi_into(buf, area.x, y, wl, area.width)
            y += 1

    for h in header:
        write(h)

    divider = apply_style(border_style, "─" * area.width)
    write(divider)

    if not rows:
        write("  " + apply_style(muted_style, empty_text))
    else:
        count = len(rows)
        visible = min(max_visible, count)
        start = max(0, min(selected - visible // 2, max(0, count - visible)))
        state.select(selected)
        state.offset = start

        if start > 0:
            write("  " + apply_style(muted_style, f"↑ {start} more above"))

        list_items: list[ListItem] = []
        for i, row in enumerate(rows):
            is_sel = i == selected
            if is_sel:
                spans = [
                    Span("  ", Style()),
                    Span(">", accent_style),
                    Span(" ", Style()),
                    Span(row.label, emphasis_style),
                ]
            else:
                spans = [Span("    ", Style()), Span(row.label, muted_style)]
            spans.extend(row.suffix_spans)
            list_items.append(ListItem(Line(spans)))

        list_area = Rect(area.x, y, area.width, visible)
        buf.grow_to(y + visible)
        List(items=list_items, highlight_symbol="", highlight_style=Style()).render(
            list_area, buf, state
        )
        y += visible

        remaining = count - (start + visible)
        if remaining > 0:
            write("  " + apply_style(muted_style, f"↓ {remaining} more below"))

    write(divider)
    write("  " + apply_style(muted_style, hint))

    return y - area.y


# Every current caller uses this exact hint text; exported so call sites
# don't each re-type the literal.
DEFAULT_HINT = "↑/↓ to move  ·  Enter to select  ·  Esc to cancel"
