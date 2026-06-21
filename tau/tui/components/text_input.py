from __future__ import annotations

import re
import unicodedata as _ud
from typing import Callable

from tau.tui.ansi import CURSOR_MARKER, visible_width, RESET, DIM, BOLD
from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, PasteEvent

# Matches any atomic input token at end-of-string (for backspace) or start-of-string (for delete-forward).
# Session-scoped (#N) and persistent (:{uuid}) variants for image/audio/video, plus paste markers.
_ATOMIC_TOKEN_END = re.compile(
    r"(?:"
    r"\[image #\d+\]|\[image:[^\]]+\]"
    r"|\[audio #\d+\]|\[audio:[^\]]+\]"
    r"|\[video #\d+\]|\[video:[^\]]+\]"
    r"|\[paste #\d+(?: \+\d+ lines| \d+ chars)\]"
    r")$"
)
_ATOMIC_TOKEN_START = re.compile(
    r"\[image #\d+\]|\[image:[^\]]+\]"
    r"|\[audio #\d+\]|\[audio:[^\]]+\]"
    r"|\[video #\d+\]|\[video:[^\]]+\]"
    r"|\[paste #\d+(?: \+\d+ lines| \d+ chars)\]"
)


class TextInput(Component):
    """
    Multiline-capable text input with cursor, history navigation, and common
    readline-style editing shortcuts.

    Keybindings
    ───────────
    Left / Right          Move cursor
    Home / ctrl+a         Move to line start
    End  / ctrl+e         Move to line end
    Backspace             Delete before cursor
    Delete / ctrl+d       Delete at cursor
    ctrl+k                Kill from cursor to end
    ctrl+u                Kill from start to cursor
    ctrl+w                Delete previous word
    ctrl+z / ctrl+y       Undo / redo (word-level grouping)
    Up / Down             Move between lines; browse history at the first/last line
    Enter                 Submit / steer mid-task when agent is busy
    alt+Enter             Queue as follow-up (fires on_followup)
    alt+Up                Dequeue queued messages (fires on_dequeue)
    \\ + Enter            Insert newline (multiline input)
    """

    def __init__(
        self,
        prefix: str = "> ",
        placeholder: str = "",
        on_submit: Callable[[str], None] | None = None,
        on_followup: Callable[[str], None] | None = None,
        on_dequeue: Callable[[], None] | None = None,
        on_paste: Callable[[], None] | None = None,
        on_paste_text: Callable[[str], None] | None = None,
        on_history_transform: Callable[[str], str] | None = None,
        padding_x: int = 0,
    ) -> None:
        self._prefix = prefix
        self._placeholder = placeholder
        self._on_submit = on_submit
        self._on_followup = on_followup
        self._on_dequeue = on_dequeue
        self.on_paste = on_paste
        self.on_paste_text = on_paste_text
        self.on_history_transform = on_history_transform
        self._padding_x = max(0, padding_x)

        self._text = ""
        self._cursor = 0
        self._line_scrolls: dict[int, int] = {}
        self._arg_hint: str = ""

        self._history: list[str] = []
        self._history_idx = -1
        self._history_draft = ""

        # Undo/redo. Each entry is a (text, cursor) snapshot of the state *before*
        # an edit group. Consecutive edits of the same kind coalesce into one group
        # (word-level for typing) so undo doesn't crawl character-by-character.
        self._undo: list[tuple[str, int]] = []
        self._redo: list[tuple[str, int]] = []
        self._last_edit: str | None = None
        self._undo_limit = 200

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def text(self) -> str:
        return self._text

    @property
    def line_count(self) -> int:
        return self._text.count("\n") + 1

    def clear(self) -> None:
        self._text = ""
        self._cursor = 0
        self._line_scrolls = {}
        self._arg_hint = ""
        self._reset_undo()

    def set_text(self, text: str) -> None:
        self._text = text
        self._cursor = len(text)
        self._line_scrolls = {}
        # Wholesale buffer replacement (history recall, external set) is a fresh
        # editing context — scope undo to the new content.
        self._reset_undo()

    def insert_at_cursor(self, text: str) -> None:
        self._insert(text)

    def focus(self) -> None:
        pass

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        prefix_w = visible_width(self._prefix)
        padding = " " * self._padding_x
        available = max(1, width - prefix_w - self._padding_x * 2)
        indent = " " * prefix_w

        if not self._text:
            cursor_block = CURSOR_MARKER + "\x1b[7m \x1b[27m"
            placeholder = self._placeholder[:available] if self._placeholder else ""
            return [BOLD + self._prefix + padding + RESET + cursor_block + DIM + placeholder + padding + RESET]

        text_lines = self._text.split("\n")
        cursor_line_idx, cursor_col = self._cursor_line_col()
        result = []

        last_line_idx = len(text_lines) - 1
        for i, line_text in enumerate(text_lines):
            prefix = self._prefix if i == 0 else indent
            scroll = self._line_scrolls.get(i, 0)
            col_in_line = cursor_col if i == cursor_line_idx else -1
            rendered, scroll = _render_line(line_text, col_in_line, available, scroll)
            self._line_scrolls[i] = scroll
            if self._arg_hint and i == last_line_idx and i == cursor_line_idx and cursor_col == len(line_text):
                rendered += DIM + self._arg_hint + RESET
            result.append(BOLD + prefix + padding + RESET + rendered + padding)

        return result

    def handle_input(self, event: InputEvent) -> bool:
        if isinstance(event, PasteEvent):
            text = event.text.replace("\r", "")
            if self.on_paste_text:
                self.on_paste_text(text)
            else:
                self._insert(text)
            return True

        if not isinstance(event, KeyEvent):
            return False

        key = str(event)

        match key:
            case "enter":
                if self._text.endswith("\\"):
                    # Replace trailing backslash with a real newline
                    self._checkpoint("newline")
                    self._last_edit = None
                    self._text = self._text[:-1] + "\n"
                    self._cursor = len(self._text)
                    self._line_scrolls = {}
                else:
                    self._submit()
            case "alt+enter":
                self._submit_followup()
            case "alt+up":
                if self._on_dequeue:
                    self._on_dequeue()
            case "ctrl+v":
                if self.on_paste:
                    self.on_paste()
                    return True
            case "backspace":
                self._backspace()
            case "delete" | "ctrl+d":
                self._delete_forward()
            case "left":
                self._move_left()
            case "right":
                self._move_right()
            case "home" | "ctrl+a":
                self._cursor = self._line_start()
                self._last_edit = None
            case "end" | "ctrl+e":
                self._cursor = self._line_end()
                self._last_edit = None
            case "ctrl+k":
                if self._cursor < len(self._text):
                    self._checkpoint("kill")
                    self._last_edit = None
                    self._text = self._text[: self._cursor]
                    self._line_scrolls = {}
            case "ctrl+u":
                if self._cursor > 0:
                    self._checkpoint("kill")
                    self._last_edit = None
                    self._text = self._text[self._cursor :]
                    self._cursor = 0
                    self._line_scrolls = {}
            case "ctrl+w":
                self._delete_word_back()
            case "ctrl+z":
                self._undo_op()
            case "ctrl+y":
                self._redo_op()
            case "up":
                self._move_up()
            case "down":
                self._move_down()
            case _:
                if event.char and not event.ctrl and not event.alt:
                    self._insert(event.char)
                else:
                    return False

        return True

    # -------------------------------------------------------------------------
    # Cursor helpers
    # -------------------------------------------------------------------------

    def _cursor_line_col(self) -> tuple[int, int]:
        before = self._text[: self._cursor]
        line_idx = before.count("\n")
        last_nl = before.rfind("\n")
        return line_idx, self._cursor - (last_nl + 1)

    def _line_start(self) -> int:
        before = self._text[: self._cursor]
        return before.rfind("\n") + 1

    def _line_end(self) -> int:
        after = self._text[self._cursor :]
        nl = after.find("\n")
        return self._cursor + (nl if nl != -1 else len(after))

    # -------------------------------------------------------------------------
    # Editing
    # -------------------------------------------------------------------------

    # ── Undo / redo ─────────────────────────────────────────────────────────
    def _reset_undo(self) -> None:
        self._undo = []
        self._redo = []
        self._last_edit = None

    def _begin_group(self) -> None:
        """Push a pre-edit snapshot, starting a fresh undo group."""
        self._undo.append((self._text, self._cursor))
        if len(self._undo) > self._undo_limit:
            self._undo.pop(0)
        self._redo.clear()

    def _checkpoint(self, kind: str) -> None:
        """Start a new undo group unless this edit continues the current ``kind``.

        Edits of the same ``kind`` in a row coalesce (e.g. a run of backspaces),
        so each undo reverts a meaningful chunk rather than one keystroke.
        """
        if not self._undo or kind != self._last_edit:
            self._begin_group()
        self._last_edit = kind

    def _undo_op(self) -> None:
        if not self._undo:
            return
        self._redo.append((self._text, self._cursor))
        self._text, self._cursor = self._undo.pop()
        self._line_scrolls = {}
        self._last_edit = None
        self._history_idx = -1

    def _redo_op(self) -> None:
        if not self._redo:
            return
        self._undo.append((self._text, self._cursor))
        self._text, self._cursor = self._redo.pop()
        self._line_scrolls = {}
        self._last_edit = None
        self._history_idx = -1

    def _insert(self, text: str) -> None:
        # Editing the buffer commits out of history/message-tree browsing, so the
        # '@' file picker and '/' command palette (both gated on _history_idx == -1)
        # work again instead of staying suppressed until the next submit.
        self._history_idx = -1
        # Undo grouping for typing is word-level: a word and its trailing spaces
        # form one group; the next word starts a fresh group. Multi-char inserts
        # (paste, @file, etc.) are each their own group.
        if len(text) == 1:
            if text.isspace():
                # Trailing space stays in the current word's group; if we weren't
                # mid-word, open a group so the space is still undoable on its own.
                if self._last_edit not in ("type", "type-space"):
                    self._begin_group()
                self._last_edit = "type-space"
            else:
                # A new word begins after spaces or any non-typing edit.
                if self._last_edit != "type":
                    self._begin_group()
                self._last_edit = "type"
        else:
            self._begin_group()
            self._last_edit = None
        self._text = self._text[: self._cursor] + text + self._text[self._cursor :]
        self._cursor += len(text)

    def _backspace(self) -> None:
        if self._cursor > 0:
            self._checkpoint("delete")
            before = self._text[: self._cursor]
            m = re.search(_ATOMIC_TOKEN_END, before)
            if m:
                start = m.start()
                self._text = self._text[:start] + self._text[self._cursor :]
                self._cursor = start
            else:
                self._text = self._text[: self._cursor - 1] + self._text[self._cursor :]
                self._cursor -= 1
            self._line_scrolls = {}

    def _delete_forward(self) -> None:
        if self._cursor < len(self._text):
            self._checkpoint("delete-fwd")
            after = self._text[self._cursor :]
            m = re.match(_ATOMIC_TOKEN_START, after)
            if m:
                self._text = self._text[: self._cursor] + after[m.end() :]
            else:
                self._text = self._text[: self._cursor] + self._text[self._cursor + 1 :]
            self._line_scrolls = {}

    def _move_left(self) -> None:
        # Moving the insertion point ends the current undo group so the next
        # edit at the new position is its own step.
        self._last_edit = None
        if self._cursor > 0:
            before = self._text[: self._cursor]
            m = re.search(_ATOMIC_TOKEN_END, before)
            if m:
                self._cursor = m.start()
            else:
                self._cursor -= 1

    def _move_right(self) -> None:
        self._last_edit = None
        if self._cursor < len(self._text):
            after = self._text[self._cursor :]
            m = re.match(_ATOMIC_TOKEN_START, after)
            if m:
                self._cursor += m.end()
            else:
                self._cursor += 1

    @staticmethod
    def _line_offset(idx: int, lines: list[str]) -> int:
        """Character offset of the start of logical line ``idx`` (newlines count as 1)."""
        return sum(len(l) + 1 for l in lines[:idx])

    def _move_up(self) -> None:
        """Move the cursor up a line; browse history when already on the first line."""
        line_idx, col = self._cursor_line_col()
        if line_idx == 0:
            self._history_prev()
            return
        self._last_edit = None
        lines = self._text.split("\n")
        new_col = min(col, len(lines[line_idx - 1]))
        self._cursor = self._line_offset(line_idx - 1, lines) + new_col

    def _move_down(self) -> None:
        """Move the cursor down a line; browse history when already on the last line."""
        line_idx, col = self._cursor_line_col()
        lines = self._text.split("\n")
        if line_idx >= len(lines) - 1:
            self._history_next()
            return
        self._last_edit = None
        new_col = min(col, len(lines[line_idx + 1]))
        self._cursor = self._line_offset(line_idx + 1, lines) + new_col

    def _delete_word_back(self) -> None:
        if self._cursor <= 0:
            return
        self._checkpoint("delete-word")
        self._last_edit = None  # each word-delete is its own undo step
        i = self._cursor
        # Skip trailing whitespace
        while i > 0 and self._text[i - 1] in (" ", "\n"):
            i -= 1
        # Treat an atomic marker immediately before the cursor as a whole word
        before = self._text[:i]
        m = re.search(_ATOMIC_TOKEN_END, before)
        if m:
            i = m.start()
        else:
            while i > 0 and self._text[i - 1] not in (" ", "\n"):
                i -= 1
        self._text = self._text[:i] + self._text[self._cursor :]
        self._cursor = i
        self._line_scrolls = {}

    # -------------------------------------------------------------------------
    # Submit / history
    # -------------------------------------------------------------------------

    def _submit(self) -> None:
        text = self._text.strip()
        if not text:
            return
        history_text = self.on_history_transform(text) if self.on_history_transform else text
        if history_text and (not self._history or self._history[-1] != history_text):
            self._history.append(history_text)
        self._history_idx = -1
        self._history_draft = ""
        self.clear()
        if self._on_submit:
            self._on_submit(text)

    def _submit_followup(self) -> None:
        text = self._text.strip()
        if not text:
            return
        history_text = self.on_history_transform(text) if self.on_history_transform else text
        if history_text and (not self._history or self._history[-1] != history_text):
            self._history.append(history_text)
        self._history_idx = -1
        self._history_draft = ""
        self.clear()
        if self._on_followup:
            self._on_followup(text)
        elif self._on_submit:
            # If no followup handler registered, fall back to normal submit
            self._on_submit(text)

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_idx == -1:
            self._history_draft = self._text
            self._history_idx = len(self._history) - 1
        elif self._history_idx > 0:
            self._history_idx -= 1
        self.set_text(self._history[self._history_idx])

    def _history_next(self) -> None:
        if self._history_idx == -1:
            return
        self._history_idx += 1
        if self._history_idx >= len(self._history):
            self._history_idx = -1
            self.set_text(self._history_draft)
        else:
            self.set_text(self._history[self._history_idx])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _char_width(ch: str) -> int:
    cp = ord(ch)
    if cp == 0 or (0x007F <= cp <= 0x009F):
        return 0
    if _ud.east_asian_width(ch) in ("W", "F"):
        return 2
    if _ud.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 1


def _render_line(text: str, cursor_col: int, available: int, scroll: int) -> tuple[str, int]:
    """
    Render one logical line. cursor_col=-1 means no cursor on this line.
    Returns (rendered_string, updated_scroll).
    """
    cursor_vis = visible_width(text[:cursor_col]) if cursor_col >= 0 else -1

    if cursor_col >= 0:
        if cursor_vis < scroll:
            scroll = cursor_vis
        elif cursor_vis >= scroll + available:
            scroll = cursor_vis - available + 1

    result = ""
    col = 0
    vis = 0
    i = 0

    while i < len(text) and col < available:
        ch = text[i]
        w = _char_width(ch)
        if vis >= scroll:
            if cursor_col >= 0 and vis == cursor_vis:
                # CURSOR_MARKER tells the Renderer to move the hardware cursor here
                result += CURSOR_MARKER + "\x1b[7m" + ch + "\x1b[27m"
            else:
                result += ch
            col += w
        vis += w
        i += 1

    if cursor_col >= 0 and cursor_col == len(text) and cursor_vis >= scroll and col < available:
        result += CURSOR_MARKER + "\x1b[7m \x1b[27m"

    return result, scroll
