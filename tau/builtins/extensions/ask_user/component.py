from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Any

from schema import FREEFORM_LABEL, AskUserOption  # type: ignore[import-not-found]

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import RESET, Style
from tau.tui.theme import LayoutTheme

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect


def _typed_char(event: KeyEvent) -> str | None:
    """Return the printable character a key event represents, or None.

    ``event.key`` is always lowercased by the parser (the original case lives
    in ``event.char``), so text entry must read ``char`` to avoid silently
    lowercasing everything the user types.
    """
    if event.char is not None and len(event.char) >= 1 and event.char.isprintable():
        return event.char
    if len(event.key) == 1 and event.key.isprintable():
        return event.key
    return None


class _AskUserComponent(Component):
    """Floating dialog: option list (single/multi-select) + optional freeform entry."""

    ML_VISIBLE_ROWS = 8
    ML_MIN_VISIBLE_ROWS = 4  # pad short buffers so the box visibly reads as multi-line

    # Below this total width there's no room for a legible side-by-side preview
    # column, so the option list just falls back to full width with no preview.
    PREVIEW_MIN_TOTAL_WIDTH = 70
    PREVIEW_GAP = 2
    PREVIEW_MIN_BOX_HEIGHT = 6

    def __init__(
        self,
        question: str,
        context: str | None,
        options: list[AskUserOption],
        allow_multiple: bool,
        allow_freeform: bool,
        multiline: bool,
        on_done: Any,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._question = question
        self._context = context
        self._options = options
        self._allow_multiple = allow_multiple
        self._allow_freeform = allow_freeform
        self._multiline = multiline
        self._on_done = on_done
        self._theme = theme or LayoutTheme()

        self._cursor = 0
        self._checked: set[int] = set()
        self._mode = "list"  # "list" | "freeform"
        self._freeform_value = ""  # single-line freeform buffer

        # Multi-line freeform buffer (used only when self._multiline).
        self._ml_lines: list[str] = [""]
        self._ml_cursor_row = 0
        self._ml_cursor_col = 0
        self._ml_scroll_top = 0

        # Index of the synthetic "Type something…" row, if present.
        self._freeform_index = len(options) if allow_freeform else -1
        self._row_count = len(options) + (1 if allow_freeform else 0)

        # No real choices — freeform is the only path, so open straight into the
        # editor with a live cursor instead of forcing an Enter on a single
        # "Type something…" row first. Applies to both single- and multi-line.
        if not self._options and self._allow_freeform:
            self._enter_freeform()

    def _enter_freeform(self, seed: str = "") -> None:
        self._mode = "freeform"
        if self._multiline:
            self._ml_lines = [seed]
            self._ml_cursor_row = 0
            self._ml_cursor_col = len(seed)
            self._ml_scroll_top = 0
        else:
            self._freeform_value = seed

    # ── Render ────────────────────────────────────────────────────────────

    def _has_preview_capable_options(self) -> bool:
        return not self._allow_multiple and any(o.preview for o in self._options)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        def _write(lines: list[str], x: int, y: int, width: int) -> int:
            row = 0
            for line in lines:
                row += parse_ansi_wrapped_into(buf, x, y + row, line, width)
            return row

        t = self._theme
        header: list[str] = []
        if self._context:
            for line in self._context.splitlines():
                header.append(f"  {t.muted.sgr()}{line}{RESET}")
            header.append("")
        header.append(f"  {t.accent.sgr()}{self._question}{RESET}")
        header.append("")

        header_rows = _write(header, area.x, area.y, area.width)
        body_y = area.y + header_rows

        show_preview = (
            self._has_preview_capable_options() and area.width >= self.PREVIEW_MIN_TOTAL_WIDTH
        )

        left_width = min(46, max(30, area.width // 3)) if show_preview else area.width
        content_lines, footer_lines = self._build_body_lines(left_width)

        if not show_preview:
            body_rows = _write(content_lines, area.x, body_y, area.width)
            footer_rows = _write(footer_lines, area.x, body_y + body_rows, area.width)
            return header_rows + body_rows + footer_rows

        right_width = area.width - left_width - self.PREVIEW_GAP

        preview: str | None = None
        if self._cursor != self._freeform_index and self._options:
            preview = self._options[self._cursor].preview

        box_height = max(len(content_lines), self.PREVIEW_MIN_BOX_HEIGHT)
        preview_box = self._build_preview_box(preview, right_width, box_height)

        left_rows = _write(content_lines, area.x, body_y, left_width)
        right_rows = _write(
            preview_box, area.x + left_width + self.PREVIEW_GAP, body_y, right_width
        )
        col_rows = max(left_rows, right_rows)

        # Footer spans the FULL width beneath both columns — it's not part of
        # either column, so it never wraps just because the left column is
        # narrow, and it visually separates from the preview box like the
        # bottom nav-hint line in the reference screenshot.
        footer_rows = _write(footer_lines, area.x, body_y + col_rows, area.width)
        return header_rows + col_rows + footer_rows

    def _build_body_lines(self, width: int) -> tuple[list[str], list[str]]:
        """Everything below the header: the option list, or whichever freeform
        editor is active. ``width`` is whatever column this ends up rendered
        in (full width, or the left column next to a preview pane) — used to
        wrap option descriptions so they don't run into the next row.

        Returns ``(content_lines, footer_lines)``. The footer is kept separate
        from the content so the caller can always render it full-width, even
        when the content itself is confined to a narrow left column.
        """
        if self._mode == "freeform" and self._multiline:
            return self._build_multiline_body()
        if self._mode == "freeform":
            return self._build_singleline_body()
        return self._build_list_body(width)

    def _build_multiline_body(self) -> tuple[list[str], list[str]]:
        inner: list[str] = []
        self._clamp_ml_scroll()
        limit = self._ml_scroll_top + self.ML_VISIBLE_ROWS
        visible = self._ml_lines[self._ml_scroll_top : limit]
        for ri, line in enumerate(visible):
            abs_row = self._ml_scroll_top + ri
            if abs_row == self._ml_cursor_row:
                before = line[: self._ml_cursor_col]
                after = line[self._ml_cursor_col :]
                inner.append(f"  {before}█{after}")
            else:
                inner.append(f"  {line}")
        # Pad with blank rows (display-only, not part of the buffer) so an
        # empty/short answer still shows several rows — a visible cue that
        # this is a multi-line editor, not a single-line box.
        for _ in range(self.ML_MIN_VISIBLE_ROWS - len(visible)):
            inner.append("")
        total = len(self._ml_lines)
        muted = self._theme.muted.sgr()
        if total > self.ML_VISIBLE_ROWS:
            pct = int(self._ml_scroll_top / max(1, total - self.ML_VISIBLE_ROWS) * 100)
            inner.append(f"  {muted}↕ {pct}%{RESET}")
        else:
            inner.append("")
        back = "Esc to cancel" if not self._options else "Esc to go back"
        footer = [
            "",
            f"  {muted}Enter to submit  ·  \\+Enter or Shift+Enter for newline  ·  {back}{RESET}",
        ]
        return inner, footer

    def _build_singleline_body(self) -> tuple[list[str], list[str]]:
        back = "Esc to cancel" if not self._options else "Esc to go back"
        content = [f"  {self._freeform_value}█"]
        footer = ["", f"  {self._theme.muted.sgr()}Enter to submit  ·  {back}{RESET}"]
        return content, footer

    # Left margin + hanging indent for wrapped description lines beneath a
    # title row — matches the "  ❯ 1. " prefix width closely enough to read as
    # aligned without having to compute each row's exact glyph width.
    DESC_INDENT = "      "

    def _build_list_body(self, width: int) -> tuple[list[str], list[str]]:
        t = self._theme
        muted = t.muted.sgr()
        success = t.success.sgr()
        selected_style = t.select_list.selected_bg or Style().reversed()
        selected_sgr = selected_style.sgr()
        normal_sgr = t.select_list.normal_label.sgr()
        arrow = t.selector_arrow

        inner: list[str] = []
        desc_width = max(width - len(self.DESC_INDENT), 10)

        for i in range(self._row_count):
            is_freeform_row = i == self._freeform_index
            title = FREEFORM_LABEL if is_freeform_row else self._options[i].title
            desc = "" if is_freeform_row else (self._options[i].description or "")
            is_cursor = i == self._cursor
            cursor_mark = arrow if is_cursor else " "

            # The whole row gets wrapped in one outer style below (reversed for
            # the cursor row, normal_label otherwise). Any inline color used
            # inside the row — e.g. the checkbox glyph — must resume that
            # outer style right after its own RESET, or the embedded reset
            # would cut the outer highlight/dim short partway through the row.
            outer_sgr = selected_sgr if is_cursor else normal_sgr

            if is_freeform_row:
                # Synthetic action row, not a countable option — keeps its own
                # arrow marker instead of a number.
                marker = f" {arrow} "
            elif self._allow_multiple:
                # Same tick glyphs as the /extensions config panel, paired with
                # the option's ordinal number.
                if i in self._checked:
                    box = "✔" if is_cursor else f"{success}✔{RESET}{outer_sgr}"
                else:
                    box = "✖" if is_cursor else f"{muted}✖{RESET}{outer_sgr}"
                marker = f"{i + 1}. {box}"
            else:
                marker = f"{i + 1}."

            row = f"  {cursor_mark} {marker} {title}"
            row = f"{outer_sgr}{row}{RESET}"
            inner.append(row)

            # Description on its own hanging-indented line(s) below the title,
            # not packed onto the title row — at left-column widths that
            # would run straight into the next option's row.
            for line in textwrap.wrap(desc, desc_width) if desc else []:
                inner.append(f"{self.DESC_INDENT}{muted}{line}{RESET}")

        hints = ["↑/↓ move", "Enter confirm", "Esc cancel"]
        if self._allow_multiple:
            hints.insert(1, "Space toggle")
        footer = ["", f"  {muted}" + "  ·  ".join(hints) + RESET]
        return inner, footer

    def _build_preview_box(self, preview: str | None, width: int, height: int) -> list[str]:
        """A bordered box of exactly ``height`` lines, sized to ``width``.

        Content beyond the available rows is truncated with a "N lines hidden"
        footer rather than growing the box — the box height is pinned to the
        paired option list so the two columns stay aligned row-for-row.
        """
        dim, reset = self._theme.border.sgr(), RESET
        inner_width = max(width - 4, 4)
        top = f"{dim}┌{'─' * (width - 2)}┐{reset}"
        bottom = f"{dim}└{'─' * (width - 2)}┘{reset}"
        content_rows = max(height - 2, 1)

        def pad(text: str) -> str:
            return text + " " * max(0, inner_width - len(text))

        def framed(text: str) -> str:
            return f"{dim}│{reset} {pad(text)} {dim}│{reset}"

        if not preview:
            body = [framed("(no preview for this option)" if content_rows else "")]
            body += [framed("") for _ in range(content_rows - len(body))]
            return [top, *body[:content_rows], bottom]

        import textwrap

        wrapped: list[str] = []
        for src_line in preview.splitlines() or [""]:
            wrapped.extend(textwrap.wrap(src_line, inner_width) or [""])

        if len(wrapped) > content_rows:
            visible = wrapped[: max(content_rows - 1, 1)]
            hidden = len(wrapped) - len(visible)
            footer = f"✂ {hidden} lines hidden".center(inner_width)
            body = [framed(line) for line in visible] + [framed(footer)]
        else:
            body = [framed(line) for line in wrapped]
            body += [framed("") for _ in range(content_rows - len(body))]

        return [top, *body[:content_rows], bottom]

    # ── Input ─────────────────────────────────────────────────────────────

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        if self._mode == "freeform":
            return self._handle_freeform_input(event)
        return self._handle_list_input(event)

    def _handle_list_input(self, event: KeyEvent) -> bool:
        match event.key:
            case "up":
                self._cursor = (self._cursor - 1) % max(1, self._row_count)
            case "down":
                self._cursor = (self._cursor + 1) % max(1, self._row_count)
            case " " | "space" if self._allow_multiple and self._cursor != self._freeform_index:
                if self._cursor in self._checked:
                    self._checked.discard(self._cursor)
                else:
                    self._checked.add(self._cursor)
            case "enter":
                if self._cursor == self._freeform_index:
                    self._enter_freeform()
                elif self._allow_multiple:
                    chosen = self._checked or {self._cursor}
                    self._on_done(
                        {
                            "kind": "selection",
                            "selections": [self._options[i].title for i in sorted(chosen)],
                        }
                    )
                else:
                    self._on_done(
                        {"kind": "selection", "selections": [self._options[self._cursor].title]}
                    )
            case "escape":
                self._on_done(None)
            case _ if self._allow_freeform and _typed_char(event) is not None:
                # Typing directly on the list view jumps straight into freeform
                # entry instead of requiring the user to navigate to and Enter
                # the "Type something…" row first.
                self._enter_freeform(seed=_typed_char(event) or "")
            case _:
                return False
        return True

    def _handle_freeform_input(self, event: KeyEvent) -> bool:
        if self._multiline:
            return self._handle_multiline_input(event)

        match event.key:
            case "enter":
                self._on_done({"kind": "freeform", "text": self._freeform_value})
            case "escape":
                # With no real choices there's no list to fall back to — Esc
                # cancels the whole prompt (mirrors the multi-line editor).
                if not self._options:
                    self._on_done(None)
                else:
                    self._mode = "list"
                    self._freeform_value = ""
            case "backspace":
                self._freeform_value = self._freeform_value[:-1]
            case _ if _typed_char(event) is not None:
                self._freeform_value += _typed_char(event) or ""
            case _:
                return False
        return True

    def _ml_insert_newline(self) -> None:
        line = self._ml_lines[self._ml_cursor_row]
        before, after = line[: self._ml_cursor_col], line[self._ml_cursor_col :]
        self._ml_lines[self._ml_cursor_row] = before
        self._ml_lines.insert(self._ml_cursor_row + 1, after)
        self._ml_cursor_row += 1
        self._ml_cursor_col = 0

    def _handle_multiline_input(self, event: KeyEvent) -> bool:
        k = event.key

        if k == "escape":
            if not self._options:
                self._on_done(None)
            else:
                self._mode = "list"
            return True
        if k == "enter":
            # Shift+Enter always inserts a newline. Ctrl+S/Ctrl+Enter aren't
            # reliable across terminals (Ctrl+S is XOFF flow control in many),
            # so plain Enter submits — unless the line ends with a trailing
            # "\", a shell-style continuation marker meaning "newline, please".
            if event.shift:
                self._ml_insert_newline()
                return True
            line = self._ml_lines[self._ml_cursor_row]
            if self._ml_cursor_col > 0 and line[self._ml_cursor_col - 1] == "\\":
                self._ml_lines[self._ml_cursor_row] = (
                    line[: self._ml_cursor_col - 1] + line[self._ml_cursor_col :]
                )
                self._ml_cursor_col -= 1
                self._ml_insert_newline()
                return True
            self._on_done({"kind": "freeform", "text": "\n".join(self._ml_lines)})
            return True
        if k == "backspace":
            if self._ml_cursor_col > 0:
                line = self._ml_lines[self._ml_cursor_row]
                self._ml_lines[self._ml_cursor_row] = (
                    line[: self._ml_cursor_col - 1] + line[self._ml_cursor_col :]
                )
                self._ml_cursor_col -= 1
            elif self._ml_cursor_row > 0:
                prev = self._ml_lines[self._ml_cursor_row - 1]
                merged = prev + self._ml_lines.pop(self._ml_cursor_row)
                self._ml_cursor_row -= 1
                self._ml_cursor_col = len(prev)
                self._ml_lines[self._ml_cursor_row] = merged
            return True
        if k == "up":
            if self._ml_cursor_row > 0:
                self._ml_cursor_row -= 1
                row_len = len(self._ml_lines[self._ml_cursor_row])
                self._ml_cursor_col = min(self._ml_cursor_col, row_len)
            return True
        if k == "down":
            if self._ml_cursor_row < len(self._ml_lines) - 1:
                self._ml_cursor_row += 1
                row_len = len(self._ml_lines[self._ml_cursor_row])
                self._ml_cursor_col = min(self._ml_cursor_col, row_len)
            return True
        if k == "left":
            if self._ml_cursor_col > 0:
                self._ml_cursor_col -= 1
            elif self._ml_cursor_row > 0:
                self._ml_cursor_row -= 1
                self._ml_cursor_col = len(self._ml_lines[self._ml_cursor_row])
            return True
        if k == "right":
            line = self._ml_lines[self._ml_cursor_row]
            if self._ml_cursor_col < len(line):
                self._ml_cursor_col += 1
            elif self._ml_cursor_row < len(self._ml_lines) - 1:
                self._ml_cursor_row += 1
                self._ml_cursor_col = 0
            return True
        if k == "home":
            self._ml_cursor_col = 0
            return True
        if k == "end":
            self._ml_cursor_col = len(self._ml_lines[self._ml_cursor_row])
            return True
        ch = _typed_char(event)
        if ch is not None:
            line = self._ml_lines[self._ml_cursor_row]
            self._ml_lines[self._ml_cursor_row] = (
                line[: self._ml_cursor_col] + ch + line[self._ml_cursor_col :]
            )
            self._ml_cursor_col += len(ch)
            return True
        return False

    def _clamp_ml_scroll(self) -> None:
        if self._ml_cursor_row < self._ml_scroll_top:
            self._ml_scroll_top = self._ml_cursor_row
        elif self._ml_cursor_row >= self._ml_scroll_top + self.ML_VISIBLE_ROWS:
            self._ml_scroll_top = self._ml_cursor_row - self.ML_VISIBLE_ROWS + 1
        # Pull the window back down when lines below it are deleted (e.g. via
        # backspace-merge) — otherwise the visible slice shrinks below
        # ML_VISIBLE_ROWS even while there's still enough content to fill it.
        max_scroll_top = max(0, len(self._ml_lines) - self.ML_VISIBLE_ROWS)
        self._ml_scroll_top = max(0, min(self._ml_scroll_top, max_scroll_top))

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
