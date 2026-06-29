"""Tests for slash-command execution policy in interactive mode."""

from __future__ import annotations

from types import SimpleNamespace

from tau.commands.registry import CommandRegistry
from tau.commands.types import CommandInfo
from tau.modes.interactive.input_handler import InputHandler


def _handler(_registry: CommandRegistry, _args: list[str]) -> None:
    return None


def _input_handler(registry: CommandRegistry) -> InputHandler:
    handler = object.__new__(InputHandler)
    handler._runtime = SimpleNamespace(commands=registry)
    return handler


def test_commands_require_idle_by_default() -> None:
    registry = CommandRegistry()
    registry.register(CommandInfo("mutate", "Mutate state", _handler))

    assert _input_handler(registry)._input_requires_idle("/mutate")


def test_busy_safe_command_does_not_require_idle() -> None:
    registry = CommandRegistry()
    registry.register(CommandInfo("panel", "Open a panel", _handler, requires_idle=False))

    assert not _input_handler(registry)._input_requires_idle("/panel details")


def test_alias_uses_command_execution_policy() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandInfo(
            "help",
            "Show help",
            _handler,
            aliases=["?"],
            requires_idle=False,
        )
    )

    assert not _input_handler(registry)._input_requires_idle("/?")


def test_terminal_and_unknown_commands_require_idle() -> None:
    handler = _input_handler(CommandRegistry())

    assert handler._input_requires_idle("!git status")
    assert handler._input_requires_idle("/unknown")
