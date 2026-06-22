from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.components.autocomplete_picker import AutocompletePicker
from tau.tui.input import InputEvent, KeyEvent

if TYPE_CHECKING:
    from tau.commands.types import CommandInfo
    from tau.tui.autocomplete import AutocompleteRegistration

_log = logging.getLogger(__name__)


class AutocompleteManager:
    """
    Owns both inline autocomplete pickers and all their async fetch state.

    Previously this logic was spread across ~130 lines inside Layout.
    Layout now creates one instance and delegates via three calls:

        sync(text, cursor, commands)   — called after every keystroke
        handle_input(event, text, cursor) -> (consumed, new_text | None)
        render(width) -> list[str]

    Two pickers are managed internally:
    - Extension autocomplete  (_ac_picker)     — trigger chars registered by providers
    - Command arg completions (_cmd_arg_picker) — activated by '/cmd <space>'
    """

    def __init__(self, max_visible: int, request_render: Callable[[], None]) -> None:
        self._ac_picker = AutocompletePicker(max_visible=max_visible)
        self._ac_providers: list[AutocompleteRegistration] = []
        self._ac_trigger_pos: int = -1
        self._ac_active_trigger: str = ""
        self._ac_pending_task: asyncio.Task | None = None  # type: ignore[type-arg]

        self._cmd_arg_picker = AutocompletePicker(max_visible=max_visible)
        self._cmd_arg_active: str = ""
        self._cmd_arg_pending_task: asyncio.Task | None = None  # type: ignore[type-arg]

        self._request_render = request_render

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._ac_picker.active or self._cmd_arg_picker.active

    def register_provider(self, reg: AutocompleteRegistration) -> None:
        self._ac_providers.append(reg)

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

    def sync(self, text: str, cursor: int, all_commands: list[CommandInfo]) -> None:
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

        if self._ac_picker.active:
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

        if self._cmd_arg_picker.active:
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

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        lines.extend(self._ac_picker.render(width))
        lines.extend(self._cmd_arg_picker.render(width))
        return lines

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

        from tau.tui.autocomplete import AutocompleteContext

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

    def _start_cmd_arg(self, cmd: CommandInfo, prefix: str) -> None:
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
