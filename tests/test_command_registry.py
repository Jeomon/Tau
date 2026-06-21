"""Tests for tau/commands/registry.py — CommandRegistry."""
from __future__ import annotations

import asyncio

from tau.commands.registry import CommandRegistry
from tau.commands.types import CommandInfo, ParsedCommand


def _cmd(name: str, aliases: list[str] | None = None, required: list[str] | None = None) -> CommandInfo:
    calls = []

    def _handler(registry, args):
        calls.append(args)

    cmd = CommandInfo(
        name=name,
        description=f"Test command {name}",
        call=_handler,
        aliases=aliases or [],
        required_arg_names=required or [],
    )
    cmd._calls = calls
    return cmd


def _registry(*cmds: CommandInfo) -> CommandRegistry:
    r = CommandRegistry(runtime=None)
    # Clear builtins so tests are isolated
    r._commands.clear()
    for c in cmds:
        r.register(c)
    return r


def run(coro):
    return asyncio.run(coro)


class TestCommandRegistryRegister:
    def test_register_adds_by_name(self):
        r = _registry()
        cmd = _cmd("hello")
        r.register(cmd)
        assert r.get("hello") is cmd

    def test_register_adds_aliases(self):
        r = _registry()
        cmd = _cmd("hello", aliases=["h", "hi"])
        r.register(cmd)
        assert r.get("h") is cmd
        assert r.get("hi") is cmd

    def test_register_overwrites_on_collision(self):
        r = _registry()
        cmd1 = _cmd("foo")
        cmd2 = _cmd("foo")
        r.register(cmd1)
        r.register(cmd2)
        assert r.get("foo") is cmd2


class TestCommandRegistryUnregister:
    def test_unregister_removes_name_and_aliases(self):
        r = _registry()
        cmd = _cmd("hello", aliases=["h"])
        r.register(cmd)
        r.unregister("hello")
        assert r.get("hello") is None
        assert r.get("h") is None

    def test_unregister_nonexistent_is_noop(self):
        r = _registry()
        r.unregister("nonexistent")  # should not raise


class TestCommandRegistryGet:
    def test_get_returns_none_for_unknown(self):
        r = _registry()
        assert r.get("unknown") is None

    def test_get_by_alias(self):
        r = _registry()
        cmd = _cmd("session", aliases=["s"])
        r.register(cmd)
        assert r.get("s") is cmd


class TestCommandRegistryList:
    def test_list_deduplicates(self):
        r = _registry()
        cmd = _cmd("hello", aliases=["h", "hi"])
        r.register(cmd)
        listed = r.list()
        assert listed.count(cmd) == 1

    def test_list_all_distinct_commands(self):
        r = _registry()
        r.register(_cmd("a"))
        r.register(_cmd("b"))
        assert len(r.list()) == 2

    def test_list_empty_registry(self):
        r = _registry()
        assert r.list() == []


class TestCommandRegistryDispatch:
    def test_dispatch_calls_handler(self):
        r = _registry()
        calls = []
        cmd = CommandInfo(
            name="ping",
            description="ping",
            call=lambda reg, args: calls.append(args),
        )
        r.register(cmd)
        result = run(r.dispatch(ParsedCommand(name="ping", args=[], raw="/ping")))
        assert result is True
        assert calls == [[]]

    def test_dispatch_unknown_returns_false(self):
        r = _registry()
        result = run(r.dispatch(ParsedCommand(name="nope", args=[], raw="/nope")))
        assert result is False

    def test_dispatch_awaits_async_handler(self):
        r = _registry()
        calls = []

        async def _async_handler(reg, args):
            calls.append(args)

        cmd = CommandInfo(name="async_cmd", description="d", call=_async_handler)
        r.register(cmd)
        run(r.dispatch(ParsedCommand(name="async_cmd", args=["x"], raw="/async_cmd x")))
        assert calls == [["x"]]

    def test_dispatch_missing_required_arg_returns_true_without_calling(self):
        r = _registry()
        calls = []
        cmd = CommandInfo(
            name="req",
            description="d",
            call=lambda reg, args: calls.append(args),
            required_arg_names=["target"],
        )
        r.register(cmd)
        result = run(r.dispatch(ParsedCommand(name="req", args=[], raw="/req")))
        assert result is True
        assert calls == []


class TestParsedCommand:
    def test_fields(self):
        pc = ParsedCommand(name="hello", args=["a", "b"], raw="/hello a b")
        assert pc.name == "hello"
        assert pc.args == ["a", "b"]
        assert pc.raw == "/hello a b"


class TestCommandInfo:
    def test_defaults(self):
        cmd = CommandInfo(name="x", description="d", call=lambda r, a: None)
        assert cmd.aliases == []
        assert cmd.required_arg_names == []
        assert cmd.argument_hint is None
