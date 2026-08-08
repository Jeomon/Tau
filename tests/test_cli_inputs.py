from __future__ import annotations

import io
from pathlib import Path

import pytest

from tau.console.cli import _build_messages, _rewrite_args, resolve_mode


class _PipedInput(io.StringIO):
    def isatty(self) -> bool:
        return False


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _streams(monkeypatch, *, stdin_tty: bool, stdout_tty: bool) -> None:
    monkeypatch.setattr("sys.stdin", _Tty() if stdin_tty else _PipedInput())
    monkeypatch.setattr("sys.stdout", _Tty() if stdout_tty else _PipedInput())


def test_rewrite_args_converts_multiple_at_files() -> None:
    assert _rewrite_args(["-p", "review", "@a.py", "@b.py"]) == [
        "-p",
        "review",
        "--file",
        "a.py",
        "--file",
        "b.py",
    ]


def test_continue_is_rewritten_to_resume_latest() -> None:
    from tau.console.cli import _RESUME_LATEST

    assert _rewrite_args(["--continue"]) == ["--resume", _RESUME_LATEST]


def test_continue_does_not_swallow_the_next_argument() -> None:
    """Unlike --resume, --continue takes no id: a following word is a value for
    whatever comes next, not a session to resume."""
    from tau.console.cli import _RESUME_LATEST

    assert _rewrite_args(["--continue", "-p", "hello"]) == [
        "--resume",
        _RESUME_LATEST,
        "-p",
        "hello",
    ]


def test_resume_still_takes_an_id() -> None:
    assert _rewrite_args(["--resume", "abc123"]) == ["--resume", "abc123"]


def test_rewrite_args_preserves_subcommands() -> None:
    assert _rewrite_args(["list", "--all"]) == ["list", "--all"]


def test_build_messages_combines_stdin_files_and_prompt(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "example.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", _PipedInput("piped text"))

    result = _build_messages(("Review this",), (source,))[0]

    assert result is not None
    assert result.startswith("piped text")
    assert f'<file path="{source}">' in result
    assert "print('ok')" in result
    assert result.endswith("Review this")


def test_resolve_mode_is_interactive_only_when_both_streams_are_ttys(monkeypatch) -> None:
    _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    assert resolve_mode(None, False, (), "text") == "interactive"


def test_resolve_mode_falls_back_to_print_when_stdout_is_piped(monkeypatch) -> None:
    _streams(monkeypatch, stdin_tty=True, stdout_tty=False)
    assert resolve_mode(None, False, (), "text") == "print"


def test_resolve_mode_falls_back_to_print_when_stdin_is_piped(monkeypatch) -> None:
    """`echo hi | tau` used to start a TUI that immediately died in
    termios.tcgetattr: raw mode needs a tty on stdin, and the piped text is
    prompt input anyway (see _build_messages)."""
    _streams(monkeypatch, stdin_tty=False, stdout_tty=True)
    assert resolve_mode(None, False, (), "text") == "print"


def test_resolve_mode_honours_an_explicit_mode_over_stream_detection(monkeypatch) -> None:
    _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    assert resolve_mode("interactive", False, (), "text") == "interactive"


def test_resolve_mode_prefers_json_for_prompts_when_output_format_is_json(monkeypatch) -> None:
    _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    assert resolve_mode(None, False, ("hi",), "json") == "json"
    assert resolve_mode(None, False, ("hi",), "text") == "print"


# ── Flag plumbing ────────────────────────────────────────────────────────────


class _Stop(Exception):
    """Ends _start once the config has been captured."""


def _config_from(monkeypatch, tmp_path, **opts):
    """Run _start far enough to capture the RuntimeConfig it builds."""
    import asyncio

    import tau.runtime.service as runtime_service
    from tau.console.cli import _start

    captured = {}

    async def fake_create(config):
        captured["config"] = config
        raise _Stop

    monkeypatch.setattr(runtime_service.Runtime, "create", staticmethod(fake_create))
    monkeypatch.chdir(tmp_path)

    base = {
        "model": None,
        "provider": None,
        "ephemeral": True,
        "mode": "print",
        "system": "",
        "append_system_prompt": "",
        "tools": None,
        "exclude_tools": None,
    }
    with pytest.raises(_Stop):
        asyncio.run(_start({**base, **opts}))
    return captured["config"]


def test_exclude_tools_is_parsed_into_a_name_set(monkeypatch, tmp_path) -> None:
    config = _config_from(monkeypatch, tmp_path, exclude_tools=" terminal , write ")

    # Whitespace trimmed, empties dropped — the same shape --tools produces.
    assert config.exclude_tools == {"terminal", "write"}
    assert config.tool_allowlist is None


def test_tools_and_exclude_tools_combine(monkeypatch, tmp_path) -> None:
    config = _config_from(
        monkeypatch, tmp_path, tools="read,write,terminal", exclude_tools="terminal"
    )

    assert config.tool_allowlist == {"read", "write", "terminal"}
    assert config.exclude_tools == {"terminal"}


def test_no_tool_flags_leaves_both_unset(monkeypatch, tmp_path) -> None:
    config = _config_from(monkeypatch, tmp_path)

    assert config.tool_allowlist is None
    assert config.exclude_tools == set()


def test_append_system_prompt_reaches_the_config(monkeypatch, tmp_path) -> None:
    config = _config_from(monkeypatch, tmp_path, append_system_prompt="HOUSE RULE")

    assert config.append_system_prompt == "HOUSE RULE"
