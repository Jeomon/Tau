"""Overlay components — picker overlay, prompt overlay, text overlay, editor overlay, form overlay."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from tau.tui.component import Component
from tau.tui.components.select_list import SelectItem, SelectList
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.utils import pad, visible_width

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

T = TypeVar("T")

# ── Box drawing helper ────────────────────────────────────────────────────────


def _box(inner_lines: list[str], title: str, width: int, theme: LayoutTheme | None = None) -> list[str]:
    """Wrap inner_lines in a Unicode border box of the given width."""
    inner_w = max(1, width - 4)  # "│ " + content + " │"

    _id: Callable[[str], str] = lambda s: s  # noqa: E731
    border = theme.border if theme is not None else _id
    emphasis = theme.emphasis if theme is not None else _id

    if title:
        t_str = f" {title} "
        tv = visible_width(t_str)
        dashes = max(0, width - 2 - tv)
        left_d = dashes // 2
        right_d = dashes - left_d
        top = border("┌" + "─" * left_d) + emphasis(t_str) + border("─" * right_d + "┐")
    else:
        top = border("┌" + "─" * (width - 2) + "┐")

    lines = [top]
    for line in inner_lines:
        lines.append(border("│") + " " + pad(line, inner_w) + " " + border("│"))
    lines.append(border("└" + "─" * (width - 2) + "┘"))
    return lines


def _default_theme() -> LayoutTheme:
    from tau.tui.theme import LayoutTheme as LT
    return LT()


# ── PickerOverlay ─────────────────────────────────────────────────────────────


class PickerOverlay[T](Component):
    """A floating modal picker: box border + optional search bar + SelectList.

    Usage::

        handle_ref = []

        def on_commit(value):
            handle_ref[0].close()
            do_something(value)

        def on_cancel():
            handle_ref[0].close()

        picker = PickerOverlay(items, title="Select model", searchable=True,
                               on_commit=on_commit, on_cancel=on_cancel)
        handle = tui.show_overlay(picker, OverlayOptions(width="70%"))
        handle_ref.append(handle)
    """

    def __init__(
        self,
        items: list[SelectItem[T]],
        title: str = "",
        on_commit: Callable[[T | None], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_preview: Callable[[T | None], None] | None = None,
        searchable: bool = False,
        max_visible: int = 8,
        initial_index: int = 0,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._selector: SelectList[T] = SelectList(items, max_visible=max_visible)
        if items:
            self._selector._selected = min(initial_index, len(items) - 1)
        self._title = title
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._on_preview = on_preview
        self._searchable = searchable
        self._query = ""
        self._theme = theme or _default_theme()

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        inner_w = max(1, width - 4)
        inner: list[str] = []
        if self._searchable:
            if self._query:
                inner.append(f"  {t.muted('⊘')} {self._query}█")
            else:
                inner.append("  " + t.muted("⊘ Search…"))
        inner.extend(self._selector.render(inner_w))
        inner.append("  " + t.muted("↑/↓ to move  ·  Enter to select  ·  Esc to cancel"))
        return _box(inner, self._title, width, t)

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        match event.key:
            case "up":
                self._selector.move_up()
                self._fire_preview()
            case "down":
                self._selector.move_down()
                self._fire_preview()
            case "enter" | "tab":
                item = self._selector.selected_item
                if self._on_commit is not None:
                    self._on_commit(item.value if item is not None else None)
            case "escape":
                if self._on_cancel is not None:
                    self._on_cancel()
            case "backspace" if self._searchable:
                self._query = self._query[:-1]
                self._selector.set_query(self._query)
            case ch if self._searchable and len(ch) == 1 and ch.isprintable():
                self._query += ch
                self._selector.set_query(self._query)
            case _:
                return False

        return True

    def invalidate(self) -> None:
        self._selector.invalidate()

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fire_preview(self) -> None:
        if self._on_preview is not None:
            item = self._selector.selected_item
            self._on_preview(item.value if item is not None else None)


# ── TextOverlay ───────────────────────────────────────────────────────────────


class TextOverlay(Component):
    """A floating read-only text display.

    Press Esc to close (calls on_close). Lines can be appended live via
    append_line() — useful for streaming status messages (e.g. OAuth flow).

    Pass non_capturing=True in OverlayOptions if this should not steal focus.
    """

    def __init__(
        self,
        lines: list[str],
        title: str = "",
        on_close: Callable[[], None] | None = None,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._lines = list(lines)
        self._title = title
        self._on_close = on_close
        self._theme = theme or _default_theme()

    # ── Public ────────────────────────────────────────────────────────────────

    def append_line(self, line: str) -> None:
        self._lines.append(line)

    def set_lines(self, lines: list[str]) -> None:
        self._lines = list(lines)

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        inner: list[str] = list(self._lines)
        if self._on_close is not None:
            inner.append("  " + t.muted("Esc to close"))
        return _box(inner, self._title, width, t)

    def handle_input(self, event: InputEvent) -> bool:
        if isinstance(event, KeyEvent) and event.key == "escape":
            if self._on_close is not None:
                self._on_close()
            return True
        return True  # swallow all input while open (modal)

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme


# ── PromptOverlay ─────────────────────────────────────────────────────────────


class PromptOverlay(Component):
    """A floating single-line text input overlay.

    Usage::

        handle_ref = []

        def on_commit(value):
            handle_ref[0].close()
            save_key(value)

        def on_cancel():
            handle_ref[0].close()

        prompt = PromptOverlay("Enter API key", on_commit=on_commit,
                               on_cancel=on_cancel, secret=True)
        handle = tui.show_overlay(prompt, OverlayOptions(width="50%"))
        handle_ref.append(handle)
    """

    def __init__(
        self,
        label: str,
        on_commit: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        secret: bool = False,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._label = label
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._secret = secret
        self._value = ""
        self._theme = theme or _default_theme()

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        display = "*" * len(self._value) if self._secret else self._value
        inner = [
            "  " + t.emphasis(self._label),
            "  " + t.muted("Enter to confirm  ·  Esc to cancel"),
            f"  {display}█",
        ]
        return _box(inner, "", width, t)

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        match event.key:
            case "enter":
                val = self._value
                if self._on_commit is not None:
                    self._on_commit(val)
            case "escape":
                if self._on_cancel is not None:
                    self._on_cancel()
            case "backspace":
                self._value = self._value[:-1]
            case ch if len(ch) == 1 and ch.isprintable():
                self._value += ch
            case _:
                return False

        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme


# ── EditorOverlay ─────────────────────────────────────────────────────────────


class EditorOverlay(Component):
    """A floating multi-line text editor overlay.

    ``Ctrl+S`` or ``Ctrl+Enter`` saves; ``Escape`` cancels.
    Arrow keys and Backspace work normally; Enter inserts a newline.
    """

    VISIBLE_ROWS = 12

    def __init__(
        self,
        title: str,
        prefill: str = "",
        on_commit: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._title = title
        self._lines: list[str] = prefill.splitlines() or [""]
        self._cursor_row = len(self._lines) - 1
        self._cursor_col = len(self._lines[-1])
        self._scroll_top = 0
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._theme = theme or _default_theme()

    # ── Cursor helpers ────────────────────────────────────────────────────────

    def _current_line(self) -> str:
        return self._lines[self._cursor_row]

    def _clamp_scroll(self) -> None:
        if self._cursor_row < self._scroll_top:
            self._scroll_top = self._cursor_row
        elif self._cursor_row >= self._scroll_top + self.VISIBLE_ROWS:
            self._scroll_top = self._cursor_row - self.VISIBLE_ROWS + 1

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        inner_w = max(1, width - 4)
        t = self._theme
        self._clamp_scroll()

        inner: list[str] = []
        visible = self._lines[self._scroll_top : self._scroll_top + self.VISIBLE_ROWS]
        for ri, line in enumerate(visible):
            abs_row = self._scroll_top + ri
            if abs_row == self._cursor_row:
                before = line[: self._cursor_col]
                after = line[self._cursor_col :]
                inner.append((before + "█" + after)[:inner_w])
            else:
                inner.append(line[:inner_w])

        # scroll indicator or blank spacer
        total = len(self._lines)
        if total > self.VISIBLE_ROWS:
            pct = int(self._scroll_top / max(1, total - self.VISIBLE_ROWS) * 100)
            inner.append(t.muted(f"↕ {pct}%"))
        else:
            inner.append("")

        inner.append(t.muted("Ctrl+S to save  ·  Esc to cancel"))
        return _box(inner, self._title, width, t)

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        k = event.key
        if k in ("ctrl+s", "ctrl+enter"):
            text = "\n".join(self._lines)
            if self._on_commit is not None:
                self._on_commit(text)
            return True
        if k == "escape":
            if self._on_cancel is not None:
                self._on_cancel()
            return True
        if k == "enter":
            line = self._lines[self._cursor_row]
            before, after = line[: self._cursor_col], line[self._cursor_col :]
            self._lines[self._cursor_row] = before
            self._lines.insert(self._cursor_row + 1, after)
            self._cursor_row += 1
            self._cursor_col = 0
            return True
        if k == "backspace":
            if self._cursor_col > 0:
                line = self._lines[self._cursor_row]
                self._lines[self._cursor_row] = (
                    line[: self._cursor_col - 1] + line[self._cursor_col :]
                )
                self._cursor_col -= 1
            elif self._cursor_row > 0:
                prev = self._lines[self._cursor_row - 1]
                merged = prev + self._lines.pop(self._cursor_row)
                self._cursor_row -= 1
                self._cursor_col = len(prev)
                self._lines[self._cursor_row] = merged
            return True
        if k == "up":
            if self._cursor_row > 0:
                self._cursor_row -= 1
                self._cursor_col = min(self._cursor_col, len(self._current_line()))
            return True
        if k == "down":
            if self._cursor_row < len(self._lines) - 1:
                self._cursor_row += 1
                self._cursor_col = min(self._cursor_col, len(self._current_line()))
            return True
        if k == "left":
            if self._cursor_col > 0:
                self._cursor_col -= 1
            elif self._cursor_row > 0:
                self._cursor_row -= 1
                self._cursor_col = len(self._current_line())
            return True
        if k == "right":
            line = self._current_line()
            if self._cursor_col < len(line):
                self._cursor_col += 1
            elif self._cursor_row < len(self._lines) - 1:
                self._cursor_row += 1
                self._cursor_col = 0
            return True
        if k == "home":
            self._cursor_col = 0
            return True
        if k == "end":
            self._cursor_col = len(self._current_line())
            return True
        if len(k) == 1 and k.isprintable():
            line = self._lines[self._cursor_row]
            self._lines[self._cursor_row] = line[: self._cursor_col] + k + line[self._cursor_col :]
            self._cursor_col += 1
            return True
        return False

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme


# ── FormOverlay ───────────────────────────────────────────────────────────────


class FormOverlay(Component):
    """A floating overlay that renders a SettingsSelector as a form.

    Supports all the same field kinds as /settings:
      - Boolean / enum cycle   (values=[...])
      - Text input             (text_input=True)
      - Select submenu         (submenu_items=[...])
      - Nested settings panel  (submenu_settings=[...])
      - Tabs                   (tabs=[("Tab A", items_a), ...])

    Usage::

        from tau.modes.interactive.components.settings_selector import SettingItem

        items = [
            SettingItem("name",  "Name",  current_value="",      text_input=True,
                        description="Display name for this profile"),
            SettingItem("env",   "Env",   current_value="prod",
                        values=["dev", "staging", "prod"],
                        description="Target environment"),
            SettingItem("model", "Model", current_value="gpt-4",
                        submenu_items=["gpt-4", "gpt-4o", "claude-3-5"],
                        description="LLM to use"),
        ]

        handle_ref = []

        def on_change(field_id, value):
            pass  # persist or react

        def on_close():
            handle_ref[0].close()

        overlay = FormOverlay(items, title="New Profile",
                              on_change=on_change, on_close=on_close)
        handle = tui.show_overlay(overlay, OverlayOptions(width="60%"))
        handle_ref.append(handle)
    """

    def __init__(
        self,
        items: list,
        title: str = "",
        on_change: Callable[[str, str], None] | None = None,
        on_close: Callable[[], None] | None = None,
        tabs: list[tuple[str, list]] | None = None,
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.modes.interactive.components.settings_selector import SettingsSelector

        self._title = title
        self._on_close = on_close
        self._theme = theme or _default_theme()
        self._selector = SettingsSelector(
            items,
            on_change=on_change or (lambda *_: None),
            theme=self._theme,
            tabs=tabs,
        )

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        inner_w = max(1, width - 4)
        inner = self._selector.render(inner_w)
        return _box(inner, self._title, width, self._theme)

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        match event.key:
            case "escape":
                if self._selector.in_submenu:
                    self._selector.cancel_submenu()
                elif self._on_close is not None:
                    self._on_close()
            case "up":
                self._selector.move_up()
            case "down":
                self._selector.move_down()
            case "enter" | " ":
                self._selector.activate()
            case "tab":
                self._selector.next_tab()
            case "backspace":
                self._selector.backspace_search()
            case ch if len(ch) == 1 and ch.isprintable():
                self._selector.append_search(ch)
            case _:
                return False

        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
        self._selector._theme = theme
