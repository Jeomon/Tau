from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tau.commands.types import CommandInfo, ParsedCommand

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


class CommandRegistry:
    """
    Holds all registered slash commands and dispatches parsed input.
    Attach a Runtime so handlers can call back into session lifecycle methods.
    """

    def __init__(self, runtime: Runtime | None = None) -> None:
        """Initialize the command registry with optional runtime context."""
        self.runtime = runtime
        self._commands: dict[str, CommandInfo] = {}
        self._source_commands: dict[str, dict[str, tuple[int, CommandInfo]]] = {}
        self._next_order = 0
        from tau.builtins.commands import get_builtin_commands

        for cmd in get_builtin_commands():
            self.register(cmd, source="builtin")

    def register(self, command: CommandInfo, source: str = "runtime") -> None:
        """Register one command source layer and rebuild active names."""
        self._next_order += 1
        self._source_commands.setdefault(source, {})[command.name] = (
            self._next_order,
            command,
        )
        self._rebuild()

    def unregister(self, name: str) -> None:
        """Remove all source layers for the visible command and its aliases."""
        cmd = self._commands.get(name)
        if cmd is None:
            return
        for commands in self._source_commands.values():
            commands.pop(cmd.name, None)
        self._rebuild()

    def replace_source(self, source: str, commands: list[CommandInfo]) -> None:
        """Atomically replace commands from one source, revealing shadowed layers."""
        self._source_commands[source] = {}
        for command in commands:
            self._next_order += 1
            self._source_commands[source][command.name] = (self._next_order, command)
        self._rebuild()

    def _rebuild(self) -> None:
        active: dict[str, tuple[int, CommandInfo]] = {}
        for commands in self._source_commands.values():
            for order, command in commands.values():
                for key in (command.name, *command.aliases):
                    current = active.get(key)
                    if current is None or order > current[0]:
                        active[key] = (order, command)
        self._commands = {key: command for key, (_, command) in active.items()}

    def get(self, name: str) -> CommandInfo | None:
        """Retrieve a command by name or alias."""
        return self._commands.get(name)

    def list(self) -> list[CommandInfo]:
        """Return all registered commands (de-duplicated by name)."""
        seen: set[str] = set()
        result: list[CommandInfo] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result

    async def dispatch(self, parsed: ParsedCommand) -> bool:
        """Invoke the matching command; return True if dispatched, False if not found."""
        cmd = self._commands.get(parsed.name)
        if cmd is None:
            return False

        missing = cmd.required_arg_names[len(parsed.args) :]
        if missing:
            if self.runtime is not None:
                plural = "s" if len(missing) > 1 else ""
                self.runtime.notify(f"Missing required argument{plural}: {', '.join(missing)}")
            return True

        result = cmd.call(self, parsed.args)
        if asyncio.iscoroutine(result):
            await result
        return True
