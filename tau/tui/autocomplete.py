from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.text import Line, Span
from tau.tui.utils import clip_to_width, fuzzy_filter, pad, visible_width

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect
    from tau.tui.theme import SelectListTheme

_log = logging.getLogger(__name__)


# ── Types / dataclasses ───────────────────────────────────────────────────────


@dataclass
class AutocompleteItem:
    """A single completion suggestion returned by a provider."""

    label: str
    description: str = ""
    # Text inserted into the editor. When None the label itself is inserted.
    insert_text: str | None = None


@dataclass
class AutocompleteContext:
    """Snapshot passed to a provider's get_items() call."""

    text: str  # full editor text at the moment of the call
    cursor_pos: int  # character index of the cursor in text
    trigger: str  # the trigger character that activated this provider (e.g. "#")
    query: str  # text typed after the trigger up to the cursor (no spaces)


@dataclass
class AutocompleteRegistration:
    """A provider registered by an extension via tau.add_autocomplete_provider()."""

    trigger: str
    get_items: Callable[
        [AutocompleteContext],
        list[AutocompleteItem] | Awaitable[list[AutocompleteItem]],
    ]
    description: str = ""


class CommandCompletion(Protocol):
    """Command metadata required by the generic autocomplete controller."""

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> list[str]: ...

    @property
    def get_argument_completions(
        self,
    ) -> (
        Callable[
            [str],
            list[AutocompleteItem] | Awaitable[list[AutocompleteItem]],
        ]
        | None
    ): ...


# ── AutocompletePicker ────────────────────────────────────────────────────────

_DEFAULT_VISIBLE_ROWS = 5


class AutocompletePicker(Component):
    """
    Fuzzy-filtered inline dropdown for extension autocomplete providers.

    Shown above the input (like the command palette) when an extension trigger
    character is detected in the editor text.  Navigation is handled here;
    commit (Tab/Enter) and dismiss (Escape) are handled by Layout.
    """

    def __init__(
        self, max_visible: int = _DEFAULT_VISIBLE_ROWS, theme: SelectListTheme | None = None
    ) -> None:
        self._all_items: list[AutocompleteItem] = []
        self._items: list[AutocompleteItem] = []
        self._selected: int = 0
        self._query: str = ""
        self._active: bool = False
        self._max_visible = max_visible

        from tau.tui.theme import SelectListTheme as _ST

        self._theme = theme or _ST()

    def set_theme(self, theme: SelectListTheme) -> None:
        self._theme = theme

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active and bool(self._items)

    @property
    def selected(self) -> AutocompleteItem | None:
        return self._items[self._selected] if self._items else None

    @property
    def line_count(self) -> int:
        return min(self._max_visible, len(self._items))

    def set_items(self, items: list[AutocompleteItem]) -> None:
        self._all_items = list(items)
        self._active = True
        self._apply_filter(self._query)

    def set_query(self, query: str) -> None:
        if query == self._query:
            return
        self._query = query
        self._apply_filter(query)

    def clear(self) -> None:
        self._all_items = []
        self._items = []
        self._selected = 0
        self._query = ""
        self._active = False

    def move_up(self) -> None:
        if self._items:
            self._selected = (self._selected - 1) % len(self._items)

    def move_down(self) -> None:
        if self._items:
            self._selected = (self._selected + 1) % len(self._items)

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        if not self.active:
            return 0

        count = len(self._items)
        visible = min(self._max_visible, count)
        start = max(0, min(self._selected - visible + 1, count - visible))

        label_w = max(
            8,
            min(
                max(visible_width(item.label) for item in self._items[start : start + visible]),
                24,
            ),
        )
        desc_w = max(0, area.width - label_w - 4)

        t = self._theme
        row = area.y

        def write(spans: list[Span]) -> None:
            nonlocal row
            buf.grow_to(row + 1)
            buf.set_line(area.x, row, Line(spans), area.width)
            row += 1

        if start > 0:
            write([Span(f"  ↑ {start} more", t.indicator)])

        for i in range(start, start + visible):
            item = self._items[i]
            is_sel = i == self._selected
            label = pad(clip_to_width(item.label, label_w), label_w)
            desc = clip_to_width(item.description, desc_w) if desc_w > 0 else ""

            if is_sel:
                write(
                    [
                        Span("  "),
                        Span(label, t.selected_label),
                        Span("  "),
                        Span(desc, t.selected_desc),
                    ]
                )
                if t.selected_bg:
                    buf.set_style(Rect(area.x, row - 1, area.width, 1), t.selected_bg)
            else:
                write(
                    [
                        Span("  "),
                        Span(label, t.normal_label),
                        Span("  "),
                        Span(desc, t.normal_desc),
                    ]
                )

        remaining = count - (start + visible)
        if remaining > 0:
            write([Span(f"  ↓ {remaining} more", t.indicator)])

        return row - area.y

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        keybindings = get_keybindings()
        if keybindings.matches(event, "tui.select.up"):
            self.move_up()
            return True
        if keybindings.matches(event, "tui.select.down"):
            self.move_down()
            return True
        return False

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _apply_filter(self, query: str) -> None:
        q = query.strip()
        if not q:
            self._items = list(self._all_items)
        else:
            self._items = fuzzy_filter(
                self._all_items,
                q,
                lambda item: item.label + " " + item.description,
            )
        self._selected = min(self._selected, len(self._items) - 1) if self._items else 0


# ── AutocompleteManager ───────────────────────────────────────────────────────


class AutocompleteManager:
    """
    Owns both inline autocomplete pickers and all their async fetch state.

    Previously this logic was spread across ~130 lines inside Layout.
    Layout now creates one instance and delegates via three calls:

        sync(text, cursor, commands)   — called after every keystroke
        handle_input(event, text, cursor) -> (consumed, new_text | None)
        render_cells(area, buf) -> int

    Two pickers are managed internally:
    - Extension autocomplete  (_ac_picker)     — trigger chars registered by providers
    - Command arg completions (_cmd_arg_picker) — activated by '/cmd <space>'
    """

    def __init__(
        self,
        max_visible: int,
        request_render: Callable[[], None],
        theme: SelectListTheme | None = None,
    ) -> None:
        self._ac_picker = AutocompletePicker(max_visible=max_visible, theme=theme)
        self._ac_providers: list[AutocompleteRegistration] = []
        self._ac_trigger_pos: int = -1
        self._ac_active_trigger: str = ""
        self._ac_pending_task: asyncio.Task | None = None  # type: ignore[type-arg]

        self._cmd_arg_picker = AutocompletePicker(max_visible=max_visible, theme=theme)
        self._cmd_arg_active: str = ""
        self._cmd_arg_pending_task: asyncio.Task | None = None  # type: ignore[type-arg]

        self._request_render = request_render

    def set_theme(self, theme: SelectListTheme) -> None:
        self._ac_picker.set_theme(theme)
        self._cmd_arg_picker.set_theme(theme)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._ac_picker.active or self._cmd_arg_picker.active

    def register_provider(self, reg: AutocompleteRegistration) -> None:
        self._ac_providers.append(reg)

    def replace_providers(self, registrations: list[AutocompleteRegistration]) -> None:
        """Replace extension providers and cancel stale in-flight completions."""
        self.clear()
        self._ac_providers = list(registrations)

    def clear(self) -> None:
        """Dismiss both pickers — called when another picker takes over."""
        if self._ac_pending_task is not None:
            self._ac_pending_task.cancel()
            self._ac_pending_task = None
        if self._cmd_arg_pending_task is not None:
            self._cmd_arg_pending_task.cancel()
            self._cmd_arg_pending_task = None
        self._ac_picker.clear()
        self._cmd_arg_picker.clear()
        self._ac_active_trigger = ""
        self._ac_trigger_pos = -1
        self._cmd_arg_active = ""

    def sync(self, text: str, cursor: int, all_commands: Sequence[CommandCompletion]) -> None:
        """
        Called after every keystroke when neither the file picker nor the
        command palette is active.  Updates whichever picker applies.
        """
        # Command argument completions: '/cmd <args>'
        if text.startswith("/"):
            space_idx = text.find(" ")
            if space_idx != -1:
                cmd_name = text[1:space_idx]
                arg_prefix = text[space_idx + 1 :]
                cmd = next(
                    (
                        c
                        for c in all_commands
                        if c.name == cmd_name or cmd_name in (c.aliases or [])
                    ),
                    None,
                )
                if cmd is not None and cmd.get_argument_completions is not None:
                    if cmd_name != self._cmd_arg_active:
                        self._cmd_arg_active = cmd_name
                        self._cmd_arg_picker.clear()
                    self._start_cmd_arg(cmd, arg_prefix)
                    self._ac_picker.clear()
                    return
        self._cmd_arg_picker.clear()
        self._cmd_arg_active = ""

        # Extension autocomplete: trigger character registered by a provider
        ac_info = self._find_trigger(text, cursor)
        if ac_info is not None:
            trigger, query, trigger_pos = ac_info
            if trigger != self._ac_active_trigger or trigger_pos != self._ac_trigger_pos:
                self._ac_active_trigger = trigger
                self._ac_trigger_pos = trigger_pos
                self._ac_picker.clear()
                self._start_ac(trigger, query, text, cursor)
            else:
                self._ac_picker.set_query(query)
        else:
            self._ac_active_trigger = ""
            self._ac_trigger_pos = -1
            self._ac_picker.clear()

    def handle_input(
        self,
        event: InputEvent,
        text: str,
        cursor: int,
    ) -> tuple[bool, str | None]:
        """
        Handle a key event when a picker is active.

        Returns (consumed, new_text):
          consumed  — True if the event was handled (prevents further dispatch)
          new_text  — the full replacement text when an item was accepted, else None
        """
        if not isinstance(event, KeyEvent):
            return False, None

        # Pickers navigate/accept on unmodified keys only. A modified arrow or
        # enter (e.g. ctrl+up for dequeue, alt+enter for follow-up, shift+enter
        # for newline) must fall through to the editor instead of being consumed
        # here — otherwise those bindings silently stop working whenever a
        # completion picker happens to be open.
        plain = not (event.alt or event.ctrl or event.meta or event.shift)

        if self._ac_picker.active and plain:
            match event.key:
                case "up":
                    self._ac_picker.move_up()
                    return True, None
                case "down":
                    self._ac_picker.move_down()
                    return True, None
                case "tab" | "enter":
                    return True, self._accept_ac(text, cursor)
                case "escape":
                    self._ac_picker.clear()
                    return True, None

        if self._cmd_arg_picker.active and plain:
            match event.key:
                case "up":
                    self._cmd_arg_picker.move_up()
                    return True, None
                case "down":
                    self._cmd_arg_picker.move_down()
                    return True, None
                case "tab" | "enter":
                    return True, self._accept_cmd_arg(text)
                case "escape":
                    self._cmd_arg_picker.clear()
                    return True, None

        return False, None

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.geometry import Rect

        row = area.y
        row += self._ac_picker.render_cells(Rect(area.x, row, area.width, 0), buf)
        row += self._cmd_arg_picker.render_cells(Rect(area.x, row, area.width, 0), buf)
        return row - area.y

    # -------------------------------------------------------------------------
    # Trigger detection
    # -------------------------------------------------------------------------

    def _find_trigger(self, text: str, cursor: int) -> tuple[str, str, int] | None:
        """Scan rightward from cursor for the nearest registered trigger char."""
        if not self._ac_providers:
            return None
        before = text[:cursor]
        triggers = {p.trigger for p in self._ac_providers}
        for i in range(len(before) - 1, -1, -1):
            ch = before[i]
            if ch == " ":
                break
            if ch in triggers:
                return ch, before[i + 1 :], i
        return None

    # -------------------------------------------------------------------------
    # Async fetch helpers
    # -------------------------------------------------------------------------

    def _start_ac(self, trigger: str, query: str, text: str, cursor: int) -> None:
        provider = next((p for p in self._ac_providers if p.trigger == trigger), None)
        if provider is None:
            return

        ctx = AutocompleteContext(text=text, cursor_pos=cursor, trigger=trigger, query=query)

        if self._ac_pending_task is not None:
            self._ac_pending_task.cancel()
            self._ac_pending_task = None

        result = provider.get_items(ctx)
        if inspect.isawaitable(result):

            async def _fetch() -> None:
                try:
                    items = await result  # type: ignore[misc]
                    if self._ac_active_trigger == trigger:
                        self._ac_picker.set_items(items)
                        self._ac_picker.set_query(query)
                        self._request_render()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _log.debug("autocomplete fetch failed", exc_info=True)

            self._ac_pending_task = asyncio.ensure_future(_fetch())
        else:
            self._ac_picker.set_items(result)  # type: ignore[arg-type]
            self._ac_picker.set_query(query)

    def _start_cmd_arg(self, cmd: CommandCompletion, prefix: str) -> None:
        if cmd.get_argument_completions is None:
            return
        if self._cmd_arg_pending_task is not None:
            self._cmd_arg_pending_task.cancel()
            self._cmd_arg_pending_task = None

        result = cmd.get_argument_completions(prefix)
        if inspect.isawaitable(result):
            active = self._cmd_arg_active

            async def _fetch() -> None:
                try:
                    items = await result  # type: ignore[misc]
                    if self._cmd_arg_active == active:
                        self._cmd_arg_picker.set_items(items)
                        self._cmd_arg_picker.set_query(prefix)
                        self._request_render()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _log.debug("cmd-arg autocomplete fetch failed", exc_info=True)

            self._cmd_arg_pending_task = asyncio.ensure_future(_fetch())
        else:
            self._cmd_arg_picker.set_items(result)  # type: ignore[arg-type]
            self._cmd_arg_picker.set_query(prefix)

    # -------------------------------------------------------------------------
    # Accept helpers
    # -------------------------------------------------------------------------

    def _accept_ac(self, text: str, cursor: int) -> str | None:
        item = self._ac_picker.selected
        if item is None:
            return None
        insert = item.insert_text if item.insert_text is not None else item.label
        new_text = (
            text[: self._ac_trigger_pos] + self._ac_active_trigger + insert + " " + text[cursor:]
        )
        self._ac_picker.clear()
        self._ac_active_trigger = ""
        self._ac_trigger_pos = -1
        return new_text

    def _accept_cmd_arg(self, text: str) -> str | None:
        item = self._cmd_arg_picker.selected
        if item is None:
            return None
        space_idx = text.find(" ")
        if space_idx == -1:
            return None
        insert = item.insert_text if item.insert_text is not None else item.label
        new_text = text[: space_idx + 1] + insert + " "
        self._cmd_arg_picker.clear()
        return new_text
