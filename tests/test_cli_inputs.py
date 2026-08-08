from __future__ import annotations

import io
from pathlib import Path

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
