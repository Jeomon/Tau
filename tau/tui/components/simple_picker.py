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

A row's label is still a single line, clipped to ``area.width`` — verified
byte-identical against the legacy ``render(width)`` path at realistic widths
(60+ columns), and only differing on terminals narrower than ~50 columns.
Callers that need more room can put the overflow in ``detail_lines``, which
become extra rows of the same ``ListItem`` (see ``ListItem.height``) instead
of being truncated; ``MultiSelectList`` wraps its descriptions that way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tau.tui.style import Style, apply_style
from tau.tui.text import Line, Span
from tau.tui.widgets.list import List, ListItem, ListState

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

# Every current caller uses this exact hint text; it is the default so call
# sites don't each re-type the literal.
DEFAULT_HINT = "↑/↓ to move  ·  Enter to select  ·  Esc to cancel"


@dataclass
class PickerRow:
    """One row: a label plus optional pre-styled spans around it (checkmark,
    description, status text, ...). Spans are independent of selection
    state — the label's own style is chosen by render_picker_cells based on
    whether this row is selected.

    ``prefix_spans`` sit between the cursor arrow and the label, which is where
    a checkbox column goes: it keeps its own colour instead of inheriting the
    label's selected/unselected style."""

    label: str
    suffix_spans: list[Span] = field(default_factory=list)
    prefix_spans: list[Span] = field(default_factory=list)
    #: Extra rows rendered under the label, already wrapped by the caller (it
    #: knows the wrap width it wants). Each becomes another row of this item —
    #: see ``ListItem.height`` — rather than being clipped off the end.
    detail_lines: list[str] = field(default_factory=list)


def render_picker_lines(
    width: int,
    *,
    header: list[str],
    rows: list[PickerRow],
    selected: int,
    state: ListState,
    max_visible: int,
    theme: LayoutTheme,
    hint: str = DEFAULT_HINT,
    empty_text: str = "No options available",
) -> list[str]:
    """Return the shared picker layout as styled lines.

    ``state`` is owned by the caller and persisted across renders (same
    ``ListState`` instance each call) so scroll position carries over.

    Every caller drew these five from the same ``LayoutTheme``, so the theme
    itself is the parameter rather than each style separately.
    """
    from tau.tui.utils import rule, visible_width, wrap

    border_style = theme.border
    muted_style = theme.muted
    accent_style = theme.accent
    emphasis_style = theme.emphasis
    arrow = theme.selector_arrow

    out: list[str] = []

    def write(line: str) -> None:
        # An over-wide line wraps onto more rows rather than being cut off,
        # matching what the cell path did to stay inside area.width.
        out.extend(wrap(line, width) if visible_width(line) > width else [line])

    for h in header:
        write(h)

    divider = rule(width, border_style)
    write(divider)

    if not rows:
        write("  " + apply_style(muted_style, empty_text))
    else:
        count = len(rows)
        visible = min(max_visible, count)
        start = max(0, min(selected - visible // 2, max(0, count - visible)))
        state.select(selected)
        state.offset = start
        # Rows with detail lines are taller than one row, so the viewport has to
        # be measured in rows, not items, or the last entries fall off the end.
        viewport_rows = sum(1 + len(row.detail_lines) for row in rows[start : start + visible])

        if start > 0:
            write("  " + apply_style(muted_style, f"\u2191 {start} more above"))

        list_items: list[ListItem] = []
        for i, row in enumerate(rows):
            is_sel = i == selected
            if is_sel:
                spans = [Span("  ", Style()), Span(arrow, accent_style), Span(" ", Style())]
            else:
                spans = [Span("    ", Style())]
            spans.extend(row.prefix_spans)
            spans.append(Span(row.label, emphasis_style if is_sel else muted_style))
            spans.extend(row.suffix_spans)
            if row.detail_lines:
                lines = [Line(spans)]
                lines += [Line([Span(detail, muted_style)]) for detail in row.detail_lines]
                list_items.append(ListItem(lines))
            else:
                list_items.append(ListItem(Line(spans)))

        widget = List(items=list_items, highlight_symbol="", highlight_style=Style())
        out.extend(widget.render_lines(width, viewport_rows, state))

        remaining = count - (start + visible)
        if remaining > 0:
            write("  " + apply_style(muted_style, f"\u2193 {remaining} more below"))

    write(divider)
    write("  " + apply_style(muted_style, hint))

    return out
