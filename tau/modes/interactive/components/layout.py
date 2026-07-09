from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tau.modes.interactive.components.command_palette import CommandPalette
from tau.modes.interactive.components.file_picker import FilePicker
from tau.modes.interactive.components.message_list import MessageBlock, MessageList
from tau.modes.interactive.components.selector_controller import SelectorController
from tau.modes.interactive.components.tree_selector import TreeRow, TreeSelectList
from tau.tui.ansi_bridge import parse_ansi_wrapped_into
from tau.tui.autocomplete import AutocompleteManager
from tau.tui.buffer import Buffer
from tau.tui.component import Component, Container, StaticComponent
from tau.tui.components.editor import EditorComponent, EditorExtras
from tau.tui.components.select_list import InlineSelector, SelectItem, SelectList
from tau.tui.components.spinner import Spinner
from tau.tui.components.text_input import TextInput
from tau.tui.geometry import Rect
from tau.tui.input import (
    InputEvent,
    KeyEvent,
    MouseEvent,
    PasteEvent,
    get_keybindings,
)  # MouseEvent kept for type narrowing
from tau.tui.style import Style, apply_style
from tau.tui.theme import LayoutTheme

if TYPE_CHECKING:
    from tau.commands.types import CommandInfo
    from tau.tui.autocomplete import AutocompleteRegistration
    from tau.tui.service import TUI, CustomOptions, OverlayHandle


def _has_editor_extras(editor: object) -> bool:
    """True if the editor provides the optional :class:`EditorExtras` surface.

    Taking ``object`` keeps the check off the concrete ``TextInput`` type, so the
    Layout's own ``self.input`` access stays fully type-checked while still
    feature-detecting custom editors at runtime.
    """
    return isinstance(editor, EditorExtras)


def _validate_editor(editor: object) -> None:
    """Warn if a custom editor doesn't satisfy the :class:`EditorComponent` core.

    Typed ``object`` so the check doesn't widen the caller's editor type.
    """
    if not isinstance(editor, EditorComponent):
        import logging

        logging.getLogger(__name__).warning(
            "Custom editor %r does not satisfy EditorComponent; the prompt may "
            "misbehave. See tau.tui.components.primitives.editor.",
            type(editor).__name__,
        )


# ── TextPrompt ────────────────────────────────────────────────────────────────


class TextPrompt:
    """
    Inline single-line text prompt shown below the editor.

    Previously lived as 5 raw state vars + open/close/handle_input/render
    spread across Layout.  Layout now holds one instance and delegates:

        prompt.open(label, on_commit, on_cancel, secret=False)
        prompt.handle_input(event) -> bool   # True = consumed (modal)
        prompt.render_cells(area, buf) -> int
        prompt.active                        # True while visible
    """

    def __init__(self) -> None:
        self._label: str = ""
        self._value: str = ""
        self._secret: bool = False
        self._on_commit: Callable[[str], None] | None = None
        self._on_cancel: Callable[[], None] | None = None

    @property
    def active(self) -> bool:
        return self._on_commit is not None

    def open(
        self,
        label: str,
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
        *,
        secret: bool = False,
    ) -> None:
        self._label = label
        self._value = ""
        self._secret = secret
        self._on_commit = on_commit
        self._on_cancel = on_cancel

    def handle_input(self, event: InputEvent) -> bool:
        """Handle a key event. Always returns True — prompt is modal."""
        if not self.active:
            return False
        if isinstance(event, PasteEvent):
            self._value += event.text.replace("\r", "").replace("\n", "")
            return True
        if not isinstance(event, KeyEvent):
            return True
        match event.key:
            case "enter":
                cb, val = self._on_commit, self._value
                self._close()
                if cb is not None:
                    cb(val)
            case "escape":
                cancel_cb = self._on_cancel
                self._close()
                if cancel_cb is not None:
                    cancel_cb()
            case "backspace":
                self._value = self._value[:-1]
            case _ if event.char is not None and event.char.isprintable():
                self._value += event.char
        return True

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.utils import BOLD, DIM, RESET

        display = ("*" * len(self._value)) if self._secret else self._value
        lines = [
            f"  {BOLD}{self._label}{RESET}  {DIM}(Enter to confirm · Esc to cancel){RESET}",
            f"  {display}█",
        ]
        return StaticComponent(lines).render_cells(area, buf)

    def _close(self) -> None:
        self._label = ""
        self._value = ""
        self._secret = False
        self._on_commit = None
        self._on_cancel = None


# ── Layout internals ──────────────────────────────────────────────────────────


class _PendingLines(Component):
    """Mutable list of pre-rendered ANSI lines for the pending-queue display."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        return StaticComponent(self.lines).render_cells(area, buf)

    def invalidate(self) -> None:
        pass


class Layout(Component):
    """
    The editor zone — dividers, text input, pickers, palette, and footer.

    Layout registers itself as a TUI child in ``__init__`` and also manages
    the named Container zones (``header``, ``status``, ``widgets_above``,
    ``widgets_below``) that appear as sibling children above/around it.
    TUI owns viewport scroll; Layout returns its full content stream without
    any clipping.

    Component assembly happens in ``__init__``:

    ┌────────────────────────────┐
    │  header zone (Container)   │  ← tui.children[0]
    │  messages                  │  ← tui.children[1]  (MessageList)
    │  spinner                   │  ← tui.children[2]
    │  pending queue             │  ← tui.children[3]
    │  status zone (Container)   │  ← tui.children[4]
    │  widgets_above (Container) │  ← tui.children[5]
    │  ──────────────────────    │  ─┐
    │  TextInput                 │   │  tui.children[6] = this Layout
    │  ──────────────────────    │   │
    │  pickers / palette         │   │
    │  footer                    │  ─┘
    │  widgets_below (Container) │  ← tui.children[7]
    └────────────────────────────┘

    Scroll (page_up / mouse-wheel) is handled by TUI._dispatch so
    handle_input() deals only with editor and picker events.
    """

    def __init__(
        self,
        tui: TUI,
        theme: LayoutTheme | None = None,
        picker_max_visible: int = 8,
        autocomplete_max_visible: int = 5,
        editor_padding_x: int = 0,
        tool_result_preview_lines: int = 5,
        cursor_blink: bool = True,
    ) -> None:
        """Initialize layout with TUI instance and default theme."""
        self._tui = tui
        self._theme = theme or LayoutTheme()
        self._picker_max_visible = picker_max_visible
        self._cursor_blink = cursor_blink

        self.messages = MessageList(
            theme=self._theme.message,
            user_prefix=self._theme.input.prefix,
            tool_result_preview_lines=tool_result_preview_lines,
        )
        self.spinner = Spinner(tui, theme=self._theme.spinner)
        self.footer: Container = Container()
        self.palette = CommandPalette(theme=self._theme.select_list)
        self.input = TextInput(
            prefix=self._theme.input.prefix,
            placeholder=self._theme.input.placeholder,
            padding_x=editor_padding_x,
            tui=tui,
            cursor_blink=cursor_blink,
        )

        # ── Public Container zones ──────────────────────────────────────────
        # Add children directly or use the helper APIs below.
        #
        #   layout.header.add_child(Banner())         # above messages
        #   layout.footer.add_child(my_widget)        # inside the footer (above status bar)
        #   layout.status.add_child(my_status)        # above editor
        #   layout.widgets_above.add_child(widget)    # between status & editor
        #   layout.widgets_below.add_child(widget)    # below editor
        self.header: Container = Container()
        self.status: Container = Container()
        self.widgets_above: Container = Container()
        self.widgets_below: Container = Container()

        self._all_commands: list[CommandInfo] = []

        # Returns True while the agent is streaming; lets handle_input distinguish
        # "ESC clears the editor" (idle) from "ESC aborts the run" (busy — must
        # fall through to the global key handler). Defaults to never-busy.
        self._is_busy: Callable[[], bool] = lambda: False

        # Pending queue display — steering/followup messages waiting to be consumed
        self._pending_lines: _PendingLines = _PendingLines()
        # Independent sources feeding the pending display. Steering/follow-up are
        # rebuilt from the engine queues on every queue-update; deferred holds
        # raw /command and !terminal inputs the user entered while the agent was
        # busy, which are replayed once the turn settles.
        self._pending_steering: list[str] = []
        self._pending_followup: list[str] = []
        self._pending_deferred: list[str] = []

        # Key-tracked widget maps — set_widget/remove_widget manage these;
        # the components are also held in the Container zones above.
        self._widgets_above_map: dict[str, Component] = {}
        self._widgets_below_map: dict[str, Component] = {}

        # Key-tracked status lines — set_status() manages this;
        # rendered as dim text immediately above the editor.
        self._status_map: dict[str, str] = {}

        # Cached submit/followup/dequeue callbacks — re-wired after editor replacement
        self._stored_submit_cb: Callable[[str], None] | None = None
        self._stored_followup_cb: Callable[[str], None] | None = None
        self._stored_dequeue_cb: Callable[[], None] | None = None
        self._custom_input_factory: Callable[[Any, Any], Any] | None = None

        # File picker — shown when user types '@'
        self.file_picker = FilePicker(theme=self._theme.select_list)
        self._at_pos: int = 0  # char index of '@' in input text when picker opened

        # Inline pickers — rendered in the content stream below the input
        self._selectors = SelectorController(tui.request_render)
        self._settings_panel: list[str] | None = None
        self._prompt = TextPrompt()
        self._oauth_status_lines: list[str] | None = None
        self._editor_row = 0
        self._editor_row_count = 0

        # Model palette — inline filtered model list for '/model <name> <provider>'

        # Extension autocomplete + command argument completions
        self._autocomplete = AutocompleteManager(
            max_visible=autocomplete_max_visible,
            request_render=tui.request_render,
            theme=self._theme.select_list,
        )

        # Register zones with TUI in render order.  widgets_above, widgets_below,
        # and _status_map are rendered by Layout itself (they sit between the
        # dividers and pickers); the remaining zones become TUI children.
        tui.add_child(self.header)
        tui.add_child(StaticComponent([""]))
        tui.add_child(self.messages)
        tui.add_child(self.spinner)
        tui.add_child(self._pending_lines)
        tui.add_child(self.status)
        tui.add_child(
            self
        )  # Layout = EditorZone (status_map + dividers + input + pickers + footer)

    @property
    def _active_selector(self) -> InlineSelector | None:
        """Compatibility bridge while selector construction remains on Layout."""
        return self._selectors.active

    @_active_selector.setter
    def _active_selector(self, selector: InlineSelector | None) -> None:
        self._selectors.active = selector

    # -------------------------------------------------------------------------
    # Attach / detach (for full-screen takeovers and TrustScreen)
    # -------------------------------------------------------------------------

    def attach(self, tui: TUI) -> None:
        """Re-add all zones to ``tui`` in the correct render order."""
        tui.add_child(self.header)
        tui.add_child(StaticComponent([""]))
        tui.add_child(self.messages)
        tui.add_child(self.spinner)
        tui.add_child(self._pending_lines)
        tui.add_child(self.status)
        tui.add_child(self)

    def detach(self, tui: TUI) -> None:
        """Remove all zones from ``tui`` (used before installing a full-screen takeover)."""
        tui.clear()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        """Render the editor zone: status-map, dividers, input, pickers, footer.

        The editor zone's own composition (dividers/input/widgets stacking)
        writes directly into buf — Container/TextInput/SelectorController/
        CommandPalette/FilePicker/AutocompleteManager/TextPrompt children all
        write straight into it via render_cells, with no string round trip.
        """
        y = area.y

        def write_lines(lines: list[str]) -> None:
            nonlocal y
            for line in lines:
                y += parse_ansi_wrapped_into(buf, area.x, y, line, area.width)

        # Status zone — keyed status lines above the editor
        if self._status_map:
            from tau.tui.utils import DIM, RESET

            write_lines([f"  {DIM}{text}{RESET}" for text in self._status_map.values()])

        # Widgets above editor
        if self.widgets_above.children:
            y += self.widgets_above.render_cells(Rect(area.x, y, area.width, 0), buf)

        # Modals that replace the input box (input disappears when a modal is active).
        # The file picker is NOT a modal — like the '/' palette it renders below the
        # input while the editor stays visible and receives the query keystrokes.
        any_modal = (
            self._selectors.is_active
            or self._settings_panel is not None
            or self._prompt.active
            or self._oauth_status_lines is not None
        )

        # Divider color: when a modal is active use plain divider; otherwise reflect input state
        _text = self.input.text
        if any_modal:
            _divider_style = self._theme.divider
        elif _text.startswith("!"):
            _divider_style = self._theme.divider_execute
        elif _text.startswith("/"):
            _divider_style = self._theme.divider_command
        else:
            _divider_style = self._theme.divider

        divider_line = apply_style(_divider_style, "─" * area.width)
        write_lines([divider_line])

        if any_modal:
            self._editor_row_count = 0
            # Modal replaces the input between the two dividers
            if self._selectors.is_active:
                y += self._selectors.render_cells(Rect(area.x, y, area.width, 0), buf)
            elif self._settings_panel is not None:
                write_lines(self._settings_panel)
            elif self._prompt.active:
                y += self._prompt.render_cells(Rect(area.x, y, area.width, 0), buf)
            elif self._oauth_status_lines is not None:
                write_lines(self._oauth_status_lines)
        else:
            # Normal: show the input editor
            self._editor_row = y - area.y
            rows = self.input.render_cells(Rect(area.x, y, area.width, 0), buf)
            self._editor_row_count = rows
            y += rows

        write_lines([divider_line])

        # Widgets below editor (only when no modal)
        if not any_modal and self.widgets_below.children:
            y += self.widgets_below.render_cells(Rect(area.x, y, area.width, 0), buf)

        # Palette, file picker, and autocomplete — always below (driven by live input
        # text); hidden during modals. The file picker renders here so it appears under
        # the input, the same way the '/' command palette does.
        any_inline = any_modal or self.palette.active or self.file_picker.active
        if not any_modal:
            y += self.palette.render_cells(Rect(area.x, y, area.width, 0), buf)
            y += self.file_picker.render_cells(Rect(area.x, y, area.width, 0), buf)
            y += self._autocomplete.render_cells(Rect(area.x, y, area.width, 0), buf)

        # Footer — hidden when any picker/modal is active
        if not any_inline:
            y += self.footer.render_cells(Rect(area.x, y, area.width, 0), buf)

        return y - area.y

    def handle_input(self, event: InputEvent) -> bool:
        """Process input event and return True if consumed.

        Scroll and mouse events are handled by TUI._dispatch before reaching here,
        so this method deals only with editor and picker navigation.
        """
        if isinstance(event, MouseEvent):
            if not event.pressed or event.button != 0:
                return False
            position = self._tui.mouse_position_for(self, event)
            if position is None:
                return False
            row, column = position
            editor_row = row - self._editor_row
            if (
                0 <= editor_row < self._editor_row_count
                and isinstance(self.input, TextInput)
                and self.input.move_cursor_to_visual(editor_row, column)
            ):
                self._tui.request_render()
                return True
            return False

        if self._selectors.handle_input(event):
            return True

        # Text prompt (accepts paste in addition to key events)
        if self._prompt.active:
            if isinstance(event, (KeyEvent, PasteEvent)):
                self._prompt.handle_input(event)
                self._tui.request_render()
            return True

        if isinstance(event, KeyEvent):
            keybindings = get_keybindings()

            # Settings panel: Esc closes
            if self._settings_panel is not None:
                if keybindings.matches(event, "tui.select.dismiss"):
                    self._settings_panel = None
                    self._tui.request_render()
                return True

            # File picker navigation (active when '@' is in progress)
            if self.file_picker.active:
                if keybindings.matches(event, "tui.select.up"):
                    self.file_picker.move_up()
                    return True
                if keybindings.matches(event, "tui.select.down"):
                    self.file_picker.move_down()
                    return True
                if keybindings.matches(event, "tui.select.confirm"):
                    self._accept_file_or_descend()
                    return True
                if keybindings.matches(event, "tui.select.dismiss"):
                    self.file_picker.close()
                    self._tui.request_render()
                    return True

            # Autocomplete navigation (extension triggers + command arg completions)
            consumed, new_text = self._autocomplete.handle_input(
                event, self.input.text, self.input.cursor
            )
            if consumed:
                if new_text is not None:
                    self.input.set_text(new_text)
                self._tui.request_render()
                return True

            # Palette navigation
            if self.palette.active:
                if keybindings.matches(event, "tui.select.up"):
                    self.palette.move_up()
                    return True
                if keybindings.matches(event, "tui.select.down"):
                    self.palette.move_down()
                    return True
                if keybindings.matches(event, "tui.select.dismiss"):
                    self.input.clear()
                    self._sync_pickers()
                    return True
                if keybindings.matches(event, "tui.select.confirm") and event.key in (
                    "enter",
                    "return",
                ):
                    self._execute_palette_selection()
                    return True
                if keybindings.matches(event, "tui.select.confirm") or event.key == "right":
                    self._accept_palette_selection()
                    return True

        result = self.input.handle_input(event)
        self._sync_pickers()
        return result

    def invalidate(self) -> None:
        """Invalidate message rendering cache."""
        self.messages.invalidate()

    # -------------------------------------------------------------------------
    # Command palette + file picker sync
    # -------------------------------------------------------------------------

    def set_commands(self, commands: list[CommandInfo]) -> None:
        """Update the list of available commands for the palette."""
        self._all_commands = list(commands)

    def set_busy_check(self, is_busy: Callable[[], bool]) -> None:
        """Wire a predicate reporting whether the agent is currently streaming.

        Used so ESC clears the editor only when idle, and otherwise propagates
        to the global key handler to abort the run.
        """
        self._is_busy = is_busy

    def set_cwd(self, cwd: Path) -> None:
        """Update the current working directory for file picker."""
        self.file_picker._root = cwd
        self.file_picker._cwd = cwd

    def _update_arg_hint(self, text: str) -> None:
        """Show per-placeholder ghost text after the cursor,
        dropping each token as the user types.
        """
        import re
        import shlex

        if not text.startswith("/") or " " not in text:
            self.input.arg_hint = ""
            return
        space_idx = text.index(" ")
        cmd_name = text[1:space_idx]
        args_part = text[space_idx + 1 :]
        cmd = next(
            (c for c in self._all_commands if c.name == cmd_name or cmd_name in (c.aliases or [])),
            None,
        )
        if cmd is None or not cmd.argument_hint:
            self.input.arg_hint = ""
            return
        hint_raw = cmd.argument_hint.strip()
        placeholders = re.findall(r"<[^>]+>", hint_raw)
        if not placeholders:
            self.input.arg_hint = "" if args_part.strip() else hint_raw
            return
        # Use shlex so quoted "multi word arg" counts as one token (matches expand.py)
        try:
            n_started = len(shlex.split(args_part))
        except ValueError:
            n_started = len(args_part.split())
        # A token being actively typed is already "consumed" — only hint what's still needed
        remaining = placeholders[n_started:]
        if remaining:
            mid_token = bool(args_part) and not args_part[-1].isspace()
            self.input.arg_hint = (" " if mid_token else "") + " ".join(remaining)
        else:
            self.input.arg_hint = ""

    def _sync_pickers(self) -> None:
        """After every keystroke, decide which picker (if any) should be visible."""
        # Arg-hints, the dynamic prefix, and history-aware pickers all need the
        # optional editor surface; a custom editor without it keeps working, just
        # without these enrichments.
        if not _has_editor_extras(self.input):
            return
        text = self.input.text
        self._update_arg_hint(text)

        # Mirror the leading '!' in the prompt prefix so the user sees
        # "! " while typing a shell command and "❯ " otherwise.
        # _visual_strip=1 hides the '!' from the rendered text so it isn't
        # shown twice — the prefix already represents it.
        in_shell = text.startswith("!")
        desired_prefix = "! " if in_shell else self._theme.input.prefix
        desired_strip = 1 if in_shell else 0
        if self.input.prefix != desired_prefix:
            self.input.prefix = desired_prefix
        if self.input.visual_strip != desired_strip:
            self.input.visual_strip = desired_strip

        # File picker: activated by '@' with no space between '@' and cursor.
        # Suppressed while browsing history so past @mentions don't hijack focus.
        at_info = self._find_at_query(text) if self.input.history_idx == -1 else None
        if at_info is not None:
            at_pos, query = at_info
            if not self.file_picker.active:
                self.file_picker.open()
                self._at_pos = at_pos
            self.file_picker.set_query(query)
            if len(query) >= 2 and not self.file_picker._entries:
                self.file_picker.close()
            self.palette.set_commands([])
            self._autocomplete.clear()
            return

        if self.file_picker.active:
            self.file_picker.close()

        # Command palette: activated by leading '/' with no space.
        # Suppressed while the user is browsing history so navigating past
        # a previously-run slash command doesn't hijack focus.
        if text.startswith("/") and " " not in text and self.input.history_idx == -1:
            self.palette.set_commands(self._all_commands)
            self.palette.set_query(text[1:])
            self._autocomplete.clear()
            return

        self.palette.set_commands([])

        # Delegate all autocomplete logic (cmd args + extension triggers) to the manager
        self._autocomplete.sync(text, self.input.cursor, self._all_commands)

    def refresh_input_state(self) -> None:
        """Resync input-dependent UI after an extension mutates the editor."""
        self._sync_pickers()
        self._tui.request_render()

    def _find_at_query(self, text: str) -> tuple[int, str] | None:
        """
        Find the rightmost '@' before the cursor whose following text has no spaces.
        Returns (at_pos, query_after_@) or None.
        """
        cursor = self.input.cursor
        before = text[:cursor]
        at_pos = before.rfind("@")
        if at_pos == -1:
            return None
        if at_pos > 0 and before[at_pos - 1] not in (" ", "\n"):
            return None
        after_at = before[at_pos + 1 :]
        if " " in after_at or ":" in after_at:
            return None
        return at_pos, after_at

    def _accept_file_or_descend(self) -> None:
        """Tab pressed while file picker is active."""
        entry = self.file_picker.enter_selected()
        text = self.input.text
        cursor = self.input.cursor

        if entry is None:
            rel = self.file_picker.cwd_relative_path
            new_text = text[: self._at_pos] + "@" + rel + "/" + text[cursor:]
            self.input.set_text(new_text)
            pass
        else:
            rel_path = self.file_picker.relative_path(entry)
            new_text = text[: self._at_pos] + "@" + rel_path + " " + text[cursor:]
            self.input.set_text(new_text)
            self.file_picker.close()

    def _accept_palette_selection(self) -> None:
        """Insert selected command into input with trailing space."""
        sel = self.palette.selected
        if sel:
            self.input.set_text(f"/{sel.name} ")
            self.palette.set_commands([])
            self._sync_pickers()

    def _execute_palette_selection(self) -> None:
        """Execute selected command immediately."""
        sel = self.palette.selected
        if sel:
            self.input.set_text(f"/{sel.name}")
            self.palette.set_commands([])
            self.input.submit()

    # -------------------------------------------------------------------------
    # Helpers for the app layer
    # -------------------------------------------------------------------------

    def add_message(self, message: object, streaming: bool = False) -> MessageBlock:
        """Add a message to the message list."""
        block = self.messages.add_message(message, streaming=streaming)
        self._tui.notify_content_added()
        return block

    def clear_messages(self) -> None:
        """Clear all messages and pending lines."""
        self.messages.clear()
        self._pending_lines.lines = []

    def dispose(self) -> None:
        """Release message caches, widgets, and editor animation."""
        self.input.dispose()
        self.spinner.dispose()
        self.messages.clear()
        self.header.clear()
        self.footer.clear()
        self.status.clear()
        self.widgets_above.clear()
        self.widgets_below.clear()

    def on_submit(self, callback: Callable[[str], None]) -> None:
        """Register callback for user submission."""
        self._stored_submit_cb = callback
        self.input.on_submit = callback

    def on_followup(self, callback: Callable[[str], None]) -> None:
        """Register callback for follow-up messages."""
        self._stored_followup_cb = callback
        self.input.on_followup = callback

    def on_dequeue(self, callback: Callable[[], None]) -> None:
        """Register callback for dequeue action."""
        self._stored_dequeue_cb = callback
        self.input.on_dequeue = callback

    # -------------------------------------------------------------------------
    # Extension customization
    # -------------------------------------------------------------------------

    def set_widget(self, id: str, widget: Any, placement: str = "above_editor") -> None:
        """Add or replace a keyed widget in the layout.

        ``widget`` can be a ``Component`` instance or a list of strings
        (rendered as a StaticComponent).  Use ``placement="below_editor"``
        to place the widget below the text input instead of above it.

        Alternatively you can add components directly to the Container zones::

            layout.widgets_above.add_child(MyWidget())
            layout.widgets_below.add_child(MyOtherWidget())
        """
        from tau.tui.component import StaticComponent

        component = widget if isinstance(widget, Component) else StaticComponent(widget)
        if placement == "below_editor":
            old = self._widgets_below_map.pop(id, None)
            if old is not None:
                self.widgets_below.remove_child(old)
            self._widgets_below_map[id] = component
            self.widgets_below.add_child(component)
        else:
            old = self._widgets_above_map.pop(id, None)
            if old is not None:
                self.widgets_above.remove_child(old)
            self._widgets_above_map[id] = component
            self.widgets_above.add_child(component)
        self._tui.request_render()

    def remove_widget(self, id: str) -> None:
        """Remove a keyed widget from the layout."""
        above = self._widgets_above_map.pop(id, None)
        if above is not None:
            self.widgets_above.remove_child(above)
        below = self._widgets_below_map.pop(id, None)
        if below is not None:
            self.widgets_below.remove_child(below)
        self._tui.request_render()

    def set_footer(self, component_or_factory: Component | Callable[[], Component] | None) -> None:
        """
        Replace the footer contents with a custom component, or clear it.

        Accepts a ``Component`` instance, a zero-argument factory, or ``None``
        to clear the footer entirely::

            layout.set_footer(MyFooter())          # component instance
            layout.set_footer(lambda: MyFooter())  # factory
            layout.set_footer(None)                # clear footer
        """
        if component_or_factory is None:
            self.footer.clear()
            self._tui.request_render()
            return
        elif callable(component_or_factory) and not isinstance(component_or_factory, Component):
            replacement = component_or_factory()
        else:
            replacement = component_or_factory  # type: ignore[assignment]

        # Replace the entire footer Container child (the Row) with the custom component
        self.footer.clear()
        self.footer.add_child(replacement)
        self._tui.request_render()

    def set_custom_footer(self, component: Component | None) -> None:
        """Backwards-compatible alias for set_footer()."""
        self.set_footer(component)

    def register_autocomplete_provider(self, registration: AutocompleteRegistration) -> None:
        """Wire an extension autocomplete provider into the layout."""
        self._autocomplete.register_provider(registration)

    def replace_autocomplete_providers(self, registrations: list[AutocompleteRegistration]) -> None:
        """Replace all extension autocomplete providers after reload."""
        self._autocomplete.replace_providers(registrations)

    def set_header(self, component_or_factory: Component | Callable[[], Component] | None) -> None:
        """
        Set the header component rendered above the message list.

        Replaces whatever is currently in the header Container with a single
        component.  For more control (multiple items, spacers…) access
        ``layout.header`` directly::

            layout.header.add_child(Banner())
            layout.header.add_child(Spacer(1))

        Pass ``None`` to clear the header entirely.
        """
        self.header.clear()
        if component_or_factory is None:
            pass
        elif callable(component_or_factory) and not isinstance(component_or_factory, Component):
            self.header.add_child(component_or_factory())
        else:
            self.header.add_child(component_or_factory)  # type: ignore[arg-type]
        self._tui.request_render()

    def set_status(self, key: str, text: str | None) -> None:
        """
        Set or clear a keyed status line shown above the editor.

        Status lines are rendered as dim text in the zone between the chat
        area and the editor — a dedicated slot separate from the footer.
        They persist until explicitly cleared::

            layout.set_status("git", "main (3 commits ahead)")
            layout.set_status("git", None)   # clear
        """
        if text is None:
            self._status_map.pop(key, None)
        else:
            self._status_map[key] = text
        self._tui.request_render()

    def set_title(self, title: str) -> None:
        """Set the terminal window title bar text."""
        self._tui.terminal.set_title(title)

    async def custom(
        self,
        factory: Callable[[TUI, Callable[[Any], None]], Component],
        options: CustomOptions | None = None,
    ) -> Any:
        """
        Show a custom component with keyboard focus.

        Show a custom component with keyboard focus.

        Without ``options.overlay=True`` (default) the TUI root is swapped
        to the custom component for a full-screen takeover; when ``done(result)``
        is called the layout is restored and the awaited value is returned::

            result = await layout.custom(
                lambda tui, done: MyScreen(on_close=done)
            )

        With ``options.overlay=True`` the component is shown as a floating
        overlay on top of the existing layout::

            result = await layout.custom(
                lambda tui, done: MyDialog(on_close=done),
                CustomOptions(overlay=True, overlay_options=OverlayOptions(width="60%")),
            )
        """
        import asyncio

        from tau.tui.service import CustomOptions as _CustomOptions

        opts = options or _CustomOptions()
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        def _done(result: Any = None) -> None:
            if not future.done():
                future.set_result(result)

        component = factory(self._tui, _done)

        if opts.overlay:
            handle = self._tui.show_overlay(component, opts.overlay_options)
            if opts.on_handle:
                opts.on_handle(handle)
            try:
                return await future
            finally:
                handle.close()
        else:
            # Full-screen takeover: detach all layout zones and install component
            self.detach(self._tui)
            self._tui.add_child(component)
            self._tui.set_focus(component)
            try:
                return await future
            finally:
                self.detach(self._tui)
                self.attach(self._tui)
                self._tui.set_focus(self)

    def get_editor_text(self) -> str:
        """Get the current text in the input editor."""
        return self.input.text

    def set_editor_text(self, text: str) -> None:
        """Replace the input editor text."""
        self.input.set_text(text)
        self.refresh_input_state()

    def paste_to_editor(self, text: str) -> None:
        """Insert text at the cursor position in the editor."""
        self.input.insert_at_cursor(text)
        self.refresh_input_state()

    def set_custom_input(self, factory: Callable[[Any, Any], Any] | None) -> None:
        """Replace the input widget with a custom implementation."""
        from tau.tui.input import get_keybindings

        self._custom_input_factory = factory
        if factory is None:
            from tau.tui.components.text_input import TextInput

            new_input: Any = TextInput(
                prefix=self._theme.input.prefix,
                placeholder=self._theme.input.placeholder,
                tui=self._tui,
                cursor_blink=self._cursor_blink,
            )
        else:
            new_input = factory(self._theme.input, get_keybindings())
            _validate_editor(new_input)
        self.input = new_input
        if self._stored_submit_cb is not None:
            self.input.on_submit = self._stored_submit_cb
        if self._stored_followup_cb is not None:
            self.input.on_followup = self._stored_followup_cb
        if self._stored_dequeue_cb is not None:
            self.input.on_dequeue = self._stored_dequeue_cb
        self._tui.request_render()

    def set_pending_queue(
        self,
        steering: list[str],
        followup: list[str],
        dequeue_hint: str = "Ctrl+↑ to edit queued",
    ) -> None:
        """Update the steering/follow-up sources and rebuild the pending display."""
        self._pending_steering = steering
        self._pending_followup = followup
        self._rebuild_pending(dequeue_hint)

    def set_deferred_queue(self, deferred: list[str]) -> None:
        """Update the deferred /command + !terminal source and rebuild the display."""
        self._pending_deferred = deferred
        self._rebuild_pending()

    def _rebuild_pending(self, dequeue_hint: str = "Alt+↑ to edit queued") -> None:
        """Rebuild the pending-messages display between spinner and input."""
        from tau.tui.utils import DIM, RESET, strip_control_chars

        lines: list[str] = []
        for label, msgs in (
            ("Steering", self._pending_steering),
            ("Follow-up", self._pending_followup),
            ("Deferred", self._pending_deferred),
        ):
            for msg in msgs:
                preview = strip_control_chars(msg)[:80]
                lines.append(f"  {DIM}{label}:{RESET} {DIM}{preview}{RESET}")
        if lines:
            lines.append(f"  {DIM}↳ {dequeue_hint}{RESET}")
        self._pending_lines.lines = lines

    def restore_queued_to_editor(self, messages: list[str]) -> None:
        """Put all queued message text back into the editor (joined by blank lines)."""
        combined = "\n\n".join(messages)
        current = self.input.text.strip()
        merged = "\n\n".join(filter(None, [combined, current]))
        self.input.set_text(merged)

    # -------------------------------------------------------------------------
    # Theme
    # -------------------------------------------------------------------------

    @property
    def theme(self) -> LayoutTheme:
        """The active layout theme."""
        return self._theme

    def set_theme(self, theme: LayoutTheme) -> None:
        """Swap the active theme and propagate it to every child component."""
        self._theme = theme
        self.messages.set_theme(theme.message)
        self.messages.set_user_prefix(theme.input.prefix)
        self.spinner.set_theme(theme.spinner)
        self.palette.set_theme(theme.select_list)
        self.file_picker.set_theme(theme.select_list)
        self._autocomplete.set_theme(theme.select_list)
        self._selectors.set_theme(theme)
        if _has_editor_extras(self.input):
            self.input.prefix = theme.input.prefix
            self.input.placeholder = theme.input.placeholder
        self._tui.request_render()

    # -------------------------------------------------------------------------
    # Inline pickers — rendered in the content stream, no floating windows
    # -------------------------------------------------------------------------

    def _make_select_list(
        self, items: list[SelectItem], current_label: str | None = None
    ) -> SelectList:
        """Build a SelectList with theme/visibility settings and optional initial selection."""
        selector = SelectList(
            items, max_visible=self._picker_max_visible, theme=self._theme.select_list
        )
        if current_label is not None:
            labels = [item.label for item in items]
            if current_label in labels:
                selector.set_selected(labels.index(current_label))
        return selector

    def open_model_selector(
        self,
        sections: list[tuple[str, str, list, str]],
        on_commit: Callable[[tuple[str, str, str]], None],
        on_cancel: Callable[[], None],
        initial: str | None = None,
    ) -> None:
        """Open the tabbed model selector modal.

        ``sections`` is a list of ``(modality, label, models, current_key)``;
        ``initial`` selects the starting modality tab. ``on_commit`` receives
        ``(model_id, provider, modality)``.
        """
        from tau.modes.interactive.components.model_selector import ModelSelector

        modal = ModelSelector(sections, initial=initial, theme=self._theme)
        self._active_selector = InlineSelector(
            kind="model",
            selector=modal,
            on_commit=on_commit,
            on_cancel=on_cancel,
        )
        self._tui.request_render()

    @property
    def theme_selector_active(self) -> bool:
        return self._active_selector is not None and self._active_selector.kind == "theme"

    def open_theme_selector(
        self,
        names: list[str],
        current: str,
        on_preview: Callable[[str], None],
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open a theme selector with live preview support."""
        from tau.modes.interactive.components.theme_selector import ThemeSelector

        def _on_select(name: str) -> None:
            self._active_selector = None
            on_commit(name)
            self._tui.request_render()

        def _on_cancel() -> None:
            self._active_selector = None
            on_cancel()
            self._tui.request_render()

        sel = ThemeSelector(
            names=names,
            current=current,
            on_select=_on_select,
            on_cancel=_on_cancel,
            on_preview=on_preview,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(kind="theme", selector=sel)
        self._tui.request_render()

    def open_effort_selector(
        self,
        levels: list,
        current: Any,
        on_commit: Callable,
        on_cancel: Callable[[], None],
    ) -> None:
        """Open an effort/thinking level selector modal."""
        from tau.modes.interactive.components.thinking_selector import ThinkingSelector

        def _on_select(level: Any) -> None:
            self._active_selector = None
            on_commit(level)
            self._tui.request_render()

        def _on_cancel() -> None:
            self._active_selector = None
            on_cancel()
            self._tui.request_render()

        sel = ThinkingSelector(
            current=current,
            available=levels,
            on_select=_on_select,
            on_cancel=_on_cancel,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(kind="effort", selector=sel)
        self._tui.request_render()

    def open_voice_selector(
        self,
        model_name: str,
        voices: list[str],
        current: str | None,
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open a voice selector for a text-to-speech model."""
        from tau.modes.interactive.components.voice_selector import VoiceSelector

        def _on_select(voice: str) -> None:
            self._active_selector = None
            on_commit(voice)
            self._tui.request_render()

        def _on_cancel() -> None:
            self._active_selector = None
            on_cancel()
            self._tui.request_render()

        selector = VoiceSelector(
            model_name=model_name,
            voices=voices,
            current=current,
            on_select=_on_select,
            on_cancel=_on_cancel,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(kind="voice", selector=selector)
        self._tui.request_render()

    def open_settings_selector(
        self,
        modal: object,
        on_cancel: Callable[[], None],
    ) -> None:
        """Open the interactive settings modal."""
        self._active_selector = InlineSelector(
            kind="settings",
            selector=modal,
            on_cancel=on_cancel,
        )
        self._tui.request_render()

    def open_resume_selector(
        self,
        sessions: list,
        on_commit: Callable[[Path], None],
        on_cancel: Callable[[], None],
        all_sessions_loader: Callable[[], list] | None = None,
        current_session_path: Path | None = None,
    ) -> None:
        """Open the session resume selector with search, scope toggle, and delete."""
        from tau.modes.interactive.components.session_selector import ResumeSelector

        modal = ResumeSelector(
            current_sessions=sessions,
            all_sessions_loader=all_sessions_loader or (lambda: []),
            current_session_path=current_session_path,
            max_visible=self._picker_max_visible,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(
            kind="resume",
            selector=modal,
            on_commit=on_commit,
            on_cancel=on_cancel,
        )
        self._tui.request_render()

    def open_tree_selector(
        self,
        items: list[SelectItem[str]],
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open a tree/list selector modal."""
        self._active_selector = InlineSelector(
            kind="tree",
            selector=self._make_select_list(items),
            on_commit=on_commit,
            on_cancel=on_cancel,
        )
        self._tui.request_render()

    def open_oauth_selector(
        self,
        mode: str,
        providers: list,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open an OAuth/API-key provider picker with fuzzy search and status indicators."""
        from tau.modes.interactive.components.oauth_selector import OAuthSelector

        def _on_select(provider_id: str) -> None:
            self._active_selector = None
            on_select(provider_id)
            self._tui.request_render()

        def _on_cancel() -> None:
            self._active_selector = None
            on_cancel()
            self._tui.request_render()

        sel = OAuthSelector(
            mode=mode,  # type: ignore[arg-type]  # callers pass Literal["login","logout"]
            providers=providers,
            on_select=_on_select,
            on_cancel=_on_cancel,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(kind="oauth", selector=sel)
        self._tui.request_render()

    def open_extension_selector(
        self,
        title: str,
        options: list[str],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open a generic option picker for extension commands."""
        from tau.modes.interactive.components.extension_selector import ExtensionSelector

        def _on_select(option: str) -> None:
            self._active_selector = None
            on_select(option)
            self._tui.request_render()

        def _on_cancel() -> None:
            self._active_selector = None
            on_cancel()
            self._tui.request_render()

        sel = ExtensionSelector(
            title=title,
            options=options,
            on_select=_on_select,
            on_cancel=_on_cancel,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(kind="extension", selector=sel)
        self._tui.request_render()

    def open_config_selector(
        self,
        entries: list,
        on_toggle: Callable,
        on_close: Callable[[], None],
    ) -> None:
        """Open the extension config selector (enable/disable per scope)."""
        from tau.modes.interactive.components.config_selector import ConfigSelector

        def _on_toggle(entry, enabled: bool) -> None:
            on_toggle(entry, enabled)
            self._tui.request_render()

        def _on_close() -> None:
            self._active_selector = None
            on_close()
            self._tui.request_render()

        sel = ConfigSelector(
            entries=entries,
            on_toggle=_on_toggle,
            on_close=_on_close,
            theme=self._theme,
        )
        self._active_selector = InlineSelector(kind="config", selector=sel)
        self._tui.request_render()

    def open_custom_selector(self, kind: str, component: Any) -> None:
        """Show an arbitrary, already-built Component inline, replacing the input editor.

        Unlike the specific open_*_selector methods above, ``component`` owns its
        entire render_cells/handle_input lifecycle (commit/cancel included) — pair
        the chosen ``kind`` with an entry in SelectorController's ``delegated`` set
        so key events reach it unmodified instead of being routed by kind-specific
        logic there.
        """
        self._active_selector = InlineSelector(kind=kind, selector=component)
        self._tui.request_render()

    def close_custom_selector(self, kind: str) -> None:
        """Clear the active inline selector if it matches ``kind`` (no-op otherwise).

        ``kind`` is checked so a selector opened after this one (e.g. the user
        cancelled and immediately opened a different modal before this callback
        ran) is never clobbered.
        """
        if self._active_selector is not None and self._active_selector.kind == kind:
            self._active_selector = None
            self._tui.request_render()

    def open_branch_tree_selector(
        self,
        rows: list[TreeRow[str]],
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open the session branch-history tree selector (role-colored, tree-connector rows)."""
        m = self._theme.message

        def role_style(role: str, text: str) -> Style:
            if role == "user":
                style = m.you_label
            elif role == "assistant":
                style = m.assistant_label
            elif role == "branch_summary":
                style = m.tool_arrow
            elif role == "tool":
                style = m.tool_result_err if text.startswith("[error]") else m.tool_result_ok
            else:
                style = m.dim
            return style

        # Size the tree to half the terminal height (min 5), not a fixed picker size.
        tree_max_visible = max(5, self._tui.terminal.height // 2)
        selected_bg = self._theme.select_list.selected_bg
        selector = TreeSelectList(
            rows,
            role_style=role_style,
            accent_style=m.you_label,
            dim_style=m.dim,
            max_visible=tree_max_visible,
            selected_bg=selected_bg,
        )
        self._active_selector = InlineSelector(
            kind="tree",
            selector=selector,
            on_commit=on_commit,
            on_cancel=on_cancel,
        )
        self._tui.request_render()

    # ── Text prompt ───────────────────────────────────────────────────────────

    def open_prompt(
        self,
        label: str,
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
        *,
        secret: bool = False,
    ) -> None:
        """Open a text input prompt modal."""
        self._prompt.open(label, on_commit, on_cancel, secret=secret)
        self._tui.request_render()

    # ── Multi-line editor overlay ─────────────────────────────────────────────

    def open_editor(
        self,
        title: str,
        prefill: str,
        on_commit: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Open a floating multi-line text editor overlay."""
        from tau.modes.interactive.components.overlays import EditorOverlay
        from tau.tui.service import OverlayOptions

        handle_ref: list[OverlayHandle] = []

        def _commit(value: str) -> None:
            if handle_ref:
                handle_ref[0].close()
            on_commit(value)

        def _cancel() -> None:
            if handle_ref:
                handle_ref[0].close()
            on_cancel()

        editor = EditorOverlay(title, prefill=prefill, on_commit=_commit, on_cancel=_cancel)
        opts = OverlayOptions(width="80%", max_height="70%", anchor="center")
        handle = self._tui.show_overlay(editor, opts)
        handle_ref.append(handle)

    # ── Settings panel ────────────────────────────────────────────────────────

    def open_settings_panel(self, lines: list[str]) -> None:
        """Show a read-only settings panel inline. Esc closes it."""
        self._settings_panel = lines
        self._tui.request_render()

    def close_settings_panel(self) -> None:
        """Close the settings panel."""
        self._settings_panel = None
        self._tui.request_render()

    # ── OAuth status (inline) ─────────────────────────────────────────────────

    def open_oauth_status(self, lines: list[str]) -> None:
        """Show OAuth progress inline in the content stream."""
        self._oauth_status_lines = list(lines)
        self._tui.request_render()

    def update_oauth_status(self, line: str) -> None:
        """Append a progress line to the OAuth status display."""
        if self._oauth_status_lines is not None:
            self._oauth_status_lines.append(line)
            self._tui.request_render()

    def close_oauth_status(self) -> None:
        """Clear the OAuth status display."""
        self._oauth_status_lines = None
        self._tui.request_render()
