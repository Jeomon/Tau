from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Any

from schema import (  # type: ignore[import-not-found]
    FREEFORM_LABEL,
    AskUserOption,
)

from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import RESET, Style
from tau.tui.text import Line, Span
from tau.tui.theme import LayoutTheme
from tau.tui.widgets.block import Block, Borders, Padding
from tau.tui.widgets.tabs import Tabs

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer


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

        # Multi-select only: text typed on the "Type something…" row is *saved*
        # alongside the ticked boxes instead of submitting on its own, so an
        # answer can be "these two options, plus this custom note".
        self._freeform_saved = ""

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

    @property
    def is_editing(self) -> bool:
        """True while the freeform editor owns the keyboard.

        The tabbed wrapper checks this before claiming arrow keys — inside the
        editor they move the text cursor, not the tab.
        """
        return self._mode == "freeform"

    def park_editor(self) -> dict | None:
        """Called when the tabbed wrapper navigates away from this question.

        Returns an answer to record, or ``None`` if there is nothing to keep.
        A question with no options is *only* an editor, so whatever has been
        typed becomes its answer — otherwise tabbing to Review would show
        "(unanswered)" for a question the user has visibly filled in. The
        buffer is left intact so returning to the tab resumes where it was.

        For questions that also have options, leaving the editor mirrors Esc:
        multi-select keeps the text alongside the ticks, single-select drops
        the draft, and neither answers the question on its own.
        """
        if self._mode != "freeform":
            return None
        text = "\n".join(self._ml_lines) if self._multiline else self._freeform_value
        if not self._options:
            return {"kind": "freeform", "text": text} if text.strip() else None
        if self._saves_freeform():
            self._freeform_saved = text.strip()
        self._mode = "list"
        self._freeform_value = ""
        return None

    def _saves_freeform(self) -> bool:
        """True when freeform text is collected *with* the ticked options.

        Only for multi-select questions that have real options: there, the
        editor's Enter saves and returns to the list. Everywhere else, freeform
        text is the whole answer and Enter submits it.
        """
        return self._allow_multiple and bool(self._options)

    def _submit_selection(self) -> None:
        """Finish a list-mode answer: ticked options plus any saved free text."""
        if self._allow_multiple:
            chosen = self._checked or (
                {self._cursor} if self._cursor != self._freeform_index else set()
            )
            selections = [self._options[i].title for i in sorted(chosen)]
        else:
            selections = [self._options[self._cursor].title]
        payload: dict[str, Any] = {"kind": "selection", "selections": list(selections)}
        if self._freeform_saved:
            payload["text"] = self._freeform_saved
            payload["selections"] = [*selections, self._freeform_saved]
        self._on_done(payload)

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

        left_rows = _write(content_lines, area.x, body_y, left_width)
        self._render_preview(
            preview,
            Rect(area.x + left_width + self.PREVIEW_GAP, body_y, right_width, box_height),
            buf,
        )
        col_rows = max(left_rows, box_height)

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
        commit = "Enter to save" if self._saves_freeform() else "Enter to submit"
        footer = [
            "",
            f"  {muted}{commit}  ·  \\+Enter or Shift+Enter for newline  ·  {back}{RESET}",
        ]
        return inner, footer

    def _build_singleline_body(self) -> tuple[list[str], list[str]]:
        back = "Esc to cancel" if not self._options else "Esc to go back"
        commit = "Enter to save" if self._saves_freeform() else "Enter to submit"
        content = [f"  {self._freeform_value}█"]
        footer = ["", f"  {self._theme.muted.sgr()}{commit}  ·  {back}{RESET}"]
        return content, footer

    # Left margin + hanging indent for wrapped description lines beneath a
    # title row — matches the "  ❯ 1. " prefix width closely enough to read as
    # aligned without having to compute each row's exact glyph width.
    DESC_INDENT = "      "

    def _build_list_body(self, width: int) -> tuple[list[str], list[str]]:
        t = self._theme
        muted = t.muted.sgr()
        success = t.success.sgr()
        accent = t.accent.sgr()
        emphasis = t.emphasis.sgr()
        arrow = t.selector_arrow

        inner: list[str] = []
        desc_width = max(width - len(self.DESC_INDENT), 10)

        for i in range(self._row_count):
            is_freeform_row = i == self._freeform_index
            title = FREEFORM_LABEL if is_freeform_row else self._options[i].title
            desc = "" if is_freeform_row else (self._options[i].description or "")
            is_cursor = i == self._cursor

            # Saved free text rides along with the ticked boxes, so show it on
            # the row — otherwise it is invisible until the answer comes back.
            if is_freeform_row and self._freeform_saved:
                saved = self._freeform_saved.replace("\n", " ")
                if len(saved) > 40:
                    saved = saved[:39] + "…"
                title = f'{FREEFORM_LABEL} "{saved}"'

            # Settings-selector style: per-span colors, no reversed background.
            # The moving cursor arrow is always accent-colored; the row label
            # is emphasis (selected) or muted (normal). Any inline color used
            # inside the row — e.g. the checkbox glyph — must resume title_sgr
            # right after its own RESET, since ANSI styles don't nest.
            title_sgr = emphasis if is_cursor else muted
            cursor_mark = f"{accent}{arrow}{RESET}{title_sgr}" if is_cursor else " "

            if is_freeform_row:
                # Synthetic action row, not a countable option: no number, and
                # no marker of its own — the moving cursor arrow is the only
                # arrow on screen. Padded to the width of an ordinal ("1.") so
                # its label lines up with the real options above it.
                if self._saves_freeform():
                    # Pad to "N. " so the tick lands in the same column as the
                    # options' checkboxes rather than one to their left.
                    tick = f"{success}✔{RESET}{title_sgr}" if self._freeform_saved else " "
                    marker = f"   {tick}"
                else:
                    marker = "  "
            elif self._allow_multiple:
                # Same tick glyphs as the /extensions config panel, paired with
                # the option's ordinal number.
                if i in self._checked:
                    box = f"{success}✔{RESET}{title_sgr}"
                else:
                    box = f"{muted}✖{RESET}{title_sgr}"
                marker = f"{i + 1}. {box}"
            else:
                marker = f"{i + 1}."

            row = f"  {cursor_mark} {marker} {title}"
            row = f"{title_sgr}{row}{RESET}"
            inner.append(row)

            # Description on its own hanging-indented line(s) below the title,
            # not packed onto the title row — at left-column widths that
            # would run straight into the next option's row.
            for line in textwrap.wrap(desc, desc_width) if desc else []:
                inner.append(f"{self.DESC_INDENT}{muted}{line}{RESET}")

        hints = ["↑/↓ move", "Enter confirm", "Esc cancel"]
        if self._allow_multiple:
            hints.insert(1, "Space toggle")
        if self._saves_freeform():
            hints.insert(-1, "Space adds text")
        footer = ["", f"  {muted}" + "  ·  ".join(hints) + RESET]
        return inner, footer

    def _render_preview(self, preview: str | None, area: Rect, buf: Buffer) -> None:
        """Draw the preview pane into ``area``.

        The frame is the shared ``Block`` widget rather than hand-assembled
        ┌─┐│└┘ strings; content goes into ``block.inner(area)``. The box height
        is pinned by the caller to the paired option list so the two columns
        stay aligned row-for-row, so anything that does not fit is truncated
        with a "N lines hidden" footer instead of growing the box.
        """
        import textwrap

        buf.grow_to(area.y + area.height)
        block = Block(
            borders=Borders.ALL,
            border_style=self._theme.border,
            padding=Padding.symmetric(1, 0),
        ).with_title(Line([Span(" Preview ", self._theme.muted)]))
        block.render(area, buf)

        inner = block.inner(area)
        if inner.width <= 0 or inner.height <= 0:
            return

        if not preview:
            buf.set_string(
                inner.x, inner.y, "(no preview for this option)"[: inner.width], self._theme.muted
            )
            return

        wrapped: list[str] = []
        for src_line in preview.splitlines() or [""]:
            wrapped.extend(textwrap.wrap(src_line, inner.width) or [""])

        if len(wrapped) > inner.height:
            visible = wrapped[: max(inner.height - 1, 1)]
            hidden = len(wrapped) - len(visible)
            footer = f"\u2702 {hidden} lines hidden".center(inner.width)
            rows = [*visible, footer]
        else:
            rows = wrapped

        for i, text in enumerate(rows[: inner.height]):
            buf.set_string(inner.x, inner.y + i, text[: inner.width], Style())

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
            case " " | "space" if self._cursor == self._freeform_index and self._saves_freeform():
                # Space opens the editor on the freeform row (Enter would too,
                # but Enter also has to mean "submit" once boxes are ticked).
                self._enter_freeform(seed=self._freeform_saved)
            case "enter":
                if self._cursor == self._freeform_index and not self._saves_freeform():
                    self._enter_freeform()
                elif self._cursor == self._freeform_index and not (
                    self._checked or self._freeform_saved
                ):
                    # Nothing ticked yet and no saved text — Enter on this row
                    # can only mean "let me type", not "submit an empty answer".
                    self._enter_freeform(seed=self._freeform_saved)
                else:
                    self._submit_selection()
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
                if self._saves_freeform():
                    # Save (or, when emptied, clear) and hand the keyboard back
                    # to the list so boxes can still be ticked.
                    self._freeform_saved = self._freeform_value
                    self._freeform_value = ""
                    self._mode = "list"
                else:
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
            if self._saves_freeform():
                self._freeform_saved = "\n".join(self._ml_lines).strip()
                self._mode = "list"
            else:
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


class _AskUserSequence(Component):
    """Several questions at once: a tab bar, one child per question, a review tab.

    Answering does not commit the whole dialog — it records the answer and moves
    on, so the user can go back with ←/→ and change their mind before submitting
    from the review tab. A single question skips all of this and behaves exactly
    like the bare :class:`_AskUserComponent`.
    """

    REVIEW_LABEL = "Review"

    def __init__(
        self,
        headers: list[str],
        children: list[_AskUserComponent],
        on_done: Any,
        theme: LayoutTheme | None = None,
        on_activity: Any = None,
    ) -> None:
        self._headers = headers
        self._children = children
        self._on_done = on_done
        self._theme = theme or LayoutTheme()
        self._on_activity = on_activity

        self._index = 0
        self._answers: dict[int, dict] = {}
        self._warning = ""

        for i, child in enumerate(children):
            child._on_done = self._make_child_callback(i)

    # ── Answer bookkeeping ────────────────────────────────────────────────

    def _make_child_callback(self, index: int) -> Any:
        def _callback(value: dict | None) -> None:
            if value is None:
                # Esc inside a question cancels the whole dialog, as in the
                # single-question case — there is no "cancel just this one".
                self._on_done(None)
                return
            self._answers[index] = value
            self._warning = ""
            self._advance_from(index)

        return _callback

    def _advance_from(self, index: int) -> None:
        """After answering, go to the next unanswered question, else to review."""
        for offset in range(1, len(self._children) + 1):
            candidate = (index + offset) % len(self._children)
            if candidate not in self._answers:
                self._index = candidate
                return
        self._index = len(self._children)  # review tab

    @property
    def _on_review(self) -> bool:
        return self._index == len(self._children)

    def _unanswered(self) -> list[int]:
        return [i for i in range(len(self._children)) if i not in self._answers]

    def _answer_text(self, index: int) -> str:
        answer = self._answers.get(index)
        if answer is None:
            return ""
        if answer.get("kind") == "freeform":
            return str(answer.get("text", "")).replace("\n", " ")
        return ", ".join(answer.get("selections", []))

    def results(self) -> list[dict | None]:
        return [self._answers.get(i) for i in range(len(self._children))]

    # ── Render ────────────────────────────────────────────────────────────

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        rows = self._render_tabs(area, buf)
        for line in self._tab_trailer():
            rows += parse_ansi_wrapped_into(buf, area.x, area.y + rows, line, area.width)

        body_area = Rect(area.x, area.y + rows, area.width, max(area.height - rows, 1))
        if self._on_review:
            for line in self._review_lines(area.width):
                rows += parse_ansi_wrapped_into(buf, area.x, area.y + rows, line, area.width)
            return rows
        return rows + self._children[self._index].render_cells(body_area, buf)

    def _render_tabs(self, area: Rect, buf: Buffer) -> int:
        """Draw the tab strip with the shared ``Tabs`` widget.

        Hand-joining the titles into one ANSI string worked, but a strip wider
        than the terminal wrapped onto a second row and pushed the body down;
        ``Tabs`` clips at the right edge instead. Each title's own spans keep
        their colour (``set_line`` patches the tab style *under* the span
        style), so an answered tab's ✔ stays green while its label follows the
        selected/unselected style.
        """
        titles: list[Line] = []
        for i, header in enumerate(self._headers):
            spans = (
                [Span("✔ ", self._theme.success)] if i in self._answers else [Span("  ", Style())]
            )
            titles.append(Line([*spans, Span(header, Style())]))
        titles.append(Line([Span("  ", Style()), Span(self.REVIEW_LABEL, Style())]))

        buf.grow_to(area.y + 1)
        Tabs(
            titles=titles,
            selected=self._index,
            style=self._theme.muted,
            highlight_style=self._theme.emphasis,
            padding_left=1,
            padding_right=1,
        ).render(Rect(area.x + 2, area.y, max(area.width - 2, 1), 1), buf)
        return 1

    def _tab_trailer(self) -> list[str]:
        """The warning line (if any) and the blank row under the tab strip."""
        lines: list[str] = []
        if self._warning:
            lines.append(f"  {self._theme.warning.sgr()}{self._warning}{RESET}")
        lines.append("")
        return lines

    def _review_lines(self, width: int) -> list[str]:
        t = self._theme
        muted, accent, success = t.muted.sgr(), t.accent.sgr(), t.success.sgr()
        lines = [f"  {accent}Review your answers{RESET}", ""]

        for i, header in enumerate(self._headers):
            answer = self._answer_text(i)
            if i in self._answers:
                text = answer or "(empty)"
                if len(text) > max(width - len(header) - 12, 20):
                    text = text[: max(width - len(header) - 13, 19)] + "…"
                lines.append(f"  {success}✔{RESET} {accent}{header}{RESET}: {text}")
            else:
                lines.append(f"  {muted}·  {header}: (unanswered){RESET}")

        hints = ["←/→ revise", "Esc cancel"]
        if not self._unanswered():
            hints.insert(0, "Enter submit")
        lines += ["", f"  {muted}" + "  ·  ".join(hints) + RESET]
        return lines

    # ── Input ─────────────────────────────────────────────────────────────

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        if self._on_activity is not None:
            self._on_activity()

        # Inside the freeform editor the child gets first refusal on every key.
        # Only Tab is taken back from it: arrows belong to the text cursor, and
        # a question with no options is *only* an editor, so without Tab there
        # would be no way off its tab short of cancelling the whole dialog.
        active = None if self._on_review else self._children[self._index]
        if active is not None and active.is_editing:
            if active.handle_input(event):
                return True
            if event.key != "tab":
                return False

        # Tab cycles forwards, Shift+Tab backwards, both wrapping through the
        # Review tab. Shift+Tab arrives as key "tab" with shift set — there is
        # no "shift+tab" key name, so it must be read off the modifier.
        if event.key in ("tab", "left", "right"):
            backwards = event.key == "left" or (event.key == "tab" and event.shift)
            self._step(-1 if backwards else 1)
            return True
        if event.key == "escape":
            self._on_done(None)
            return True

        if self._on_review:
            if event.key == "enter":
                missing = self._unanswered()
                if missing:
                    names = ", ".join(self._headers[i] for i in missing)
                    self._warning = f"Still unanswered: {names}"
                    return True
                self._on_done({"kind": "sequence", "answers": self.results()})
            return True

        return active.handle_input(event) if active is not None else False

    def _step(self, delta: int) -> None:
        """Move ``delta`` tabs, wrapping across the questions and Review."""
        self._leave_current()
        self._index = (self._index + delta) % (len(self._children) + 1)
        self._warning = ""

    def _leave_current(self) -> None:
        """Park the question being navigated away from.

        A draft typed into an editor is recorded as that question's answer
        rather than discarded — otherwise tabbing to Review shows
        "(unanswered)" for a question the user has visibly filled in.
        """
        if self._on_review:
            return
        parked = self._children[self._index].park_editor()
        if parked is not None:
            self._answers[self._index] = parked

    def invalidate(self) -> None:
        for child in self._children:
            child.invalidate()

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
        for child in self._children:
            child.set_theme(theme)
