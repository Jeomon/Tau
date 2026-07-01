from __future__ import annotations

import re
import unicodedata as _ud
from bisect import bisect_right
from collections.abc import Callable

import grapheme

from tau.tui.component import Component
from tau.tui.input import InputEvent, Key, KeyEvent, PasteEvent, get_keybindings
from tau.tui.utils import BOLD, CURSOR_MARKER, DIM, RESET, cursor_block, visible_width

# Matches any atomic input token at end-of-string (for backspace)
# or start-of-string (for delete-forward).
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
    alt/ctrl + Left/Right Move by word
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
        # Transient override (e.g. extension status text). When None the
        # configured placeholder above is shown.
        self._placeholder_override: str | None = None
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
        # Wrap width from the most recent render(), used so Up/Down can move
        # between soft-wrapped visual rows the same way they appear on screen.
        # Large sentinel means "no wrap known yet" (behaves as unwrapped).
        self._last_available: int = 1 << 30

        # How the text cursor cell is drawn. Defaults to the reverse-video block;
        # extensions (e.g. voice input) may swap in an animated/coloured cell and
        # restore this default afterwards.
        self.cursor_cell: Callable[[str], str] = cursor_block

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

        # How many leading chars to hide in the rendered display (kept in _text
        # for submission). Set to 1 when the prefix has consumed the leading '!'.
        self._visual_strip: int = 0

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def text(self) -> str:
        return self._text

    @property
    def line_count(self) -> int:
        return self._text.count("\n") + 1

    # ── Public editor interface (see tau/tui/components/editor.py) ─────────────
    # Public accessors over the private storage so the Layout/UIContext talk to a
    # documented surface (EditorComponent / EditorExtras) rather than internals.

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def prefix(self) -> str:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        self._prefix = value

    @property
    def placeholder(self) -> str:
        return self._placeholder

    @placeholder.setter
    def placeholder(self, value: str) -> None:
        self._placeholder = value

    @property
    def arg_hint(self) -> str:
        return self._arg_hint

    @arg_hint.setter
    def arg_hint(self, value: str) -> None:
        self._arg_hint = value

    @property
    def visual_strip(self) -> int:
        return self._visual_strip

    @visual_strip.setter
    def visual_strip(self, value: int) -> None:
        self._visual_strip = value

    @property
    def history_idx(self) -> int:
        return self._history_idx

    @property
    def history(self) -> list[str]:
        """Return a snapshot of submitted editor history."""
        return list(self._history)

    def replace_history(self, entries: list[str], *, limit: int = 500) -> None:
        """Replace submitted editor history without exposing internal storage."""
        self._history = list(entries[-max(1, limit) :])
        self._history_idx = -1
        self._history_draft = ""

    @property
    def on_submit(self) -> Callable[[str], None] | None:
        return self._on_submit

    @on_submit.setter
    def on_submit(self, cb: Callable[[str], None] | None) -> None:
        self._on_submit = cb

    @property
    def on_followup(self) -> Callable[[str], None] | None:
        return self._on_followup

    @on_followup.setter
    def on_followup(self, cb: Callable[[str], None] | None) -> None:
        self._on_followup = cb

    @property
    def on_dequeue(self) -> Callable[[], None] | None:
        return self._on_dequeue

    @on_dequeue.setter
    def on_dequeue(self, cb: Callable[[], None] | None) -> None:
        self._on_dequeue = cb

    def submit(self) -> None:
        """Submit the current buffer (as if Enter were pressed)."""
        self._submit()

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

    def backspace(self) -> None:
        """Delete the token/grapheme immediately before the cursor.

        Public surface over :meth:`_backspace` so an extension can retract a
        character it inserted (e.g. the voice extension undoing an optimistically
        echoed space) without reaching into private editing internals.
        """
        self._backspace()

    def set_placeholder_override(self, text: str | None) -> None:
        """Temporarily replace the placeholder (None restores the configured one)."""
        self._placeholder_override = text

    def focus(self) -> None:
        pass

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        prefix_w = visible_width(self._prefix)
        padding = " " * self._padding_x
        available = max(1, width - prefix_w - self._padding_x * 2)
        self._last_available = available
        indent = " " * prefix_w

        # Strip leading chars that the prefix has already represented visually.
        display_text = self._text[self._visual_strip :] if self._visual_strip else self._text

        if not display_text:
            empty_cursor = CURSOR_MARKER + self.cursor_cell(" ")
            effective_placeholder = (
                self._placeholder_override
                if self._placeholder_override is not None
                else self._placeholder
            )
            placeholder = effective_placeholder[:available] if effective_placeholder else ""
            return [
                BOLD
                + self._prefix
                + padding
                + RESET
                + empty_cursor
                + DIM
                + placeholder
                + padding
                + RESET
            ]

        text_lines = display_text.split("\n")
        cursor_line_idx, cursor_col = self._cursor_line_col()
        # Adjust column for the hidden leading chars on line 0.
        if self._visual_strip and cursor_line_idx == 0:
            cursor_col = max(0, cursor_col - self._visual_strip)
        result = []

        last_line_idx = len(text_lines) - 1
        for i, line_text in enumerate(text_lines):
            line_prefix = self._prefix if i == 0 else indent
            col_in_line = cursor_col if i == cursor_line_idx else -1
            segments = _render_line_wrapped(line_text, col_in_line, available, self.cursor_cell)
            for j, seg in enumerate(segments):
                seg_prefix = line_prefix if j == 0 else indent
                if (
                    self._arg_hint
                    and i == last_line_idx
                    and i == cursor_line_idx
                    and cursor_col == len(line_text)
                    and j == len(segments) - 1
                ):
                    seg += DIM + self._arg_hint + RESET
                result.append(BOLD + seg_prefix + padding + RESET + seg + padding)

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

        keybindings = get_keybindings()

        # Modified combos are listed before their bare counterparts; matching is
        # exact on modifiers, so order is for readability, not correctness.
        if keybindings.matches(event, "tui.input.newline"):
            self._insert("\n")
        elif keybindings.matches(event, "tui.input.submit"):
            if self._text.endswith("\\"):
                # Replace trailing backslash with a real newline
                self._checkpoint("newline")
                self._last_edit = None
                self._text = self._text[:-1] + "\n"
                self._cursor = len(self._text)
                self._line_scrolls = {}
            else:
                self._submit()
        elif keybindings.matches(event, "app.message.followup"):
            self._submit_followup()
        elif keybindings.matches(event, "app.message.dequeue"):
            if self._on_dequeue:
                self._on_dequeue()
        elif event.matches(Key.ctrl("v")):
            if self.on_paste:
                self.on_paste()
                return True
        elif event.matches(Key.BACKSPACE):
            self._backspace()
        elif event.matches(Key.DELETE) or (self._text and event.matches(Key.ctrl("d"))):
            self._delete_forward()
        elif event.matches(Key.alt(Key.LEFT), Key.ctrl(Key.LEFT)):
            self._word_left()
        elif event.matches(Key.alt(Key.RIGHT), Key.ctrl(Key.RIGHT)):
            self._word_right()
        elif event.matches(Key.LEFT):
            self._move_left()
        elif event.matches(Key.RIGHT):
            self._move_right()
        elif event.matches(Key.HOME, Key.ctrl("a")):
            self._cursor = self._line_start()
            self._last_edit = None
        elif event.matches(Key.END) or (self._text and event.matches(Key.ctrl("e"))):
            self._cursor = self._line_end()
            self._last_edit = None
        elif event.matches(Key.ctrl("k")):
            if self._cursor < len(self._text):
                self._checkpoint("kill")
                self._last_edit = None
                self._text = self._text[: self._cursor]
                self._line_scrolls = {}
        elif keybindings.matches(event, "tui.input.clear"):
            if self._cursor > 0:
                self._checkpoint("kill")
                self._last_edit = None
                self._text = self._text[self._cursor :]
                self._cursor = 0
                self._line_scrolls = {}
        elif keybindings.matches(event, "tui.input.word_back"):
            self._delete_word_back()
        elif event.matches(Key.ctrl("z")):
            self._undo_op()
        elif event.matches(Key.ctrl("y")):
            self._redo_op()
        elif event.matches(Key.UP):
            self._move_up()
        elif event.matches(Key.DOWN):
            self._move_down()
        else:
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
        if "\n" in text:
            self._line_scrolls = {}

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
                # Delete the whole grapheme cluster before the cursor.
                start = grapheme.safe_split_index(self._text, self._cursor - 1)
                self._text = self._text[:start] + self._text[self._cursor :]
                self._cursor = start
            self._line_scrolls = {}

    def _delete_forward(self) -> None:
        if self._cursor < len(self._text):
            self._checkpoint("delete-fwd")
            after = self._text[self._cursor :]
            m = re.match(_ATOMIC_TOKEN_START, after)
            if m:
                self._text = self._text[: self._cursor] + after[m.end() :]
            else:
                # Delete the whole grapheme cluster at the cursor.
                cluster = next(iter(grapheme.graphemes(after)), "")
                self._text = self._text[: self._cursor] + after[len(cluster) :]  # type: ignore[arg-type]
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
                self._cursor = grapheme.safe_split_index(self._text, self._cursor - 1)

    def _move_right(self) -> None:
        self._last_edit = None
        if self._cursor < len(self._text):
            after = self._text[self._cursor :]
            m = re.match(_ATOMIC_TOKEN_START, after)
            if m:
                self._cursor += m.end()
            else:
                cluster = next(iter(grapheme.graphemes(after)), "")
                self._cursor += len(cluster)  # type: ignore[arg-type]

    def _word_left(self) -> None:
        """Move the cursor to the start of the previous word."""
        self._last_edit = None
        i = self._cursor
        while i > 0 and self._text[i - 1] in (" ", "\n"):
            i -= 1
        while i > 0 and self._text[i - 1] not in (" ", "\n"):
            i -= 1
        self._cursor = i

    def _word_right(self) -> None:
        """Move the cursor to the end of the next word."""
        self._last_edit = None
        n = len(self._text)
        i = self._cursor
        while i < n and self._text[i] in (" ", "\n"):
            i += 1
        while i < n and self._text[i] not in (" ", "\n"):
            i += 1
        self._cursor = i

    @staticmethod
    def _line_offset(idx: int, lines: list[str]) -> int:
        """Character offset of the start of logical line ``idx`` (newlines count as 1)."""
        return sum(len(ln) + 1 for ln in lines[:idx])

    def _move_up(self) -> None:
        """Move up a visual (soft-wrapped) row; browse history at the very first row."""
        if not self._move_visual_row(-1):
            self._history_prev()

    def _move_down(self) -> None:
        """Move down a visual (soft-wrapped) row; browse history at the very last row."""
        if not self._move_visual_row(1):
            self._history_next()

    def _move_visual_row(self, direction: int) -> bool:
        """Move the cursor to the visual row above (-1) or below (+1) the current one.

        Accounts for soft-wrap: a single logical line that wraps into several
        on-screen rows is treated as several rows here, so Up/Down first walks
        within a wrapped line before crossing into the next logical line.
        Returns False when there is no such row (cursor already on the first/
        last visual row of the whole buffer) so the caller can fall back to
        history navigation.
        """
        lines = self._text.split("\n")
        line_idx, col = self._cursor_line_col()
        available = self._last_available

        starts_per_line = [_wrap_row_starts(line, available) for line in lines]

        starts = starts_per_line[line_idx]
        ri = bisect_right(starts, col) - 1
        row_start = starts[ri]

        flat_idx = sum(len(s) for s in starts_per_line[:line_idx]) + ri
        total_rows = sum(len(s) for s in starts_per_line)

        target_flat = flat_idx + direction
        if target_flat < 0 or target_flat >= total_rows:
            return False

        remaining = target_flat
        target_li, target_ri = 0, 0
        for li, s in enumerate(starts_per_line):
            if remaining < len(s):
                target_li, target_ri = li, remaining
                break
            remaining -= len(s)

        target_starts = starts_per_line[target_li]
        target_start = target_starts[target_ri]
        target_end = (
            target_starts[target_ri + 1]
            if target_ri + 1 < len(target_starts)
            else len(lines[target_li])
        )

        self._last_edit = None
        rel = col - row_start
        new_col = target_start + min(rel, target_end - target_start)
        self._cursor = self._line_offset(target_li, lines) + new_col
        return True

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


def _wrap_row_starts(text: str, available: int) -> list[int]:
    """Character indices where each visual (soft-wrapped) row of ``text`` begins.

    Always starts with 0. Mirrors the wrap decision in ``_render_line_wrapped``
    so cursor math (Up/Down) lands on the same rows that are actually drawn.
    """
    if available <= 0:
        return [0]
    starts = [0]
    col = 0
    for i, ch in enumerate(text):
        w = _char_width(ch)
        if w > 0 and col + w > available and col > 0:
            starts.append(i)
            col = 0
        col += w
    return starts


def _render_line_wrapped(
    text: str,
    cursor_col: int,
    available: int,
    cursor_cell: Callable[[str], str] = cursor_block,
) -> list[str]:
    """
    Render one logical line with word-wrap instead of horizontal scrolling.
    cursor_col=-1 means no cursor on this line.
    Returns a list of visual-line strings (without prefix/padding).
    """
    cursor_vis = visible_width(text[:cursor_col]) if cursor_col >= 0 else -1

    visual_lines: list[str] = []
    current = ""
    col = 0  # visual width on the current visual line
    vis = 0  # visual position within the logical line
    i = 0

    while i < len(text):
        ch = text[i]
        w = _char_width(ch)

        # Wrap before this character when it would overflow (only for visible chars)
        if w > 0 and col + w > available and col > 0:
            visual_lines.append(current)
            current = ""
            col = 0

        if cursor_col >= 0 and vis == cursor_vis:
            # CURSOR_MARKER tells the Renderer to move the hardware cursor here
            current += CURSOR_MARKER + cursor_cell(ch)
        else:
            current += ch

        col += w
        vis += w
        i += 1

    # End-of-text cursor (cursor is past the last character)
    if cursor_col >= 0 and cursor_col == len(text):
        if col >= available and col > 0:
            visual_lines.append(current)
            current = CURSOR_MARKER + cursor_cell(" ")
        else:
            current += CURSOR_MARKER + cursor_cell(" ")

    visual_lines.append(current)
    return visual_lines
