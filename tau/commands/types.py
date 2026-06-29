from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.commands.registry import CommandRegistry
    from tau.tui.autocomplete import AutocompleteItem


@dataclass
class CommandInfo:
    """Metadata for a registered command."""

    name: str
    description: str
    call: Callable[[CommandRegistry, list[str]], Awaitable[None] | None]
    aliases: list[str] = field(default_factory=list)
    argument_hint: str | None = None
    get_argument_completions: Callable[[str], list[AutocompleteItem]] | None = None
    required_arg_names: list[str] = field(default_factory=list)
    requires_idle: bool = True
    """Whether interactive dispatch must wait until the agent is idle.

    Set this to ``False`` only for commands that do not mutate agent-turn state,
    such as opening a UI panel or displaying read-only information.
    """
    """Names of the leading positional args that must be present, in order.

    Any args beyond these are treated as optional. Declare required args
    before optional ones, since this only checks a minimum count.
    """


@dataclass
class ParsedCommand:
    """Result of parsing a command string into name, args, and raw input."""

    name: str
    args: list[str]
    raw: str
