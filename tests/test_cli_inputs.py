from __future__ import annotations

import io
from pathlib import Path

from tau.console.cli import _build_initial_message, _rewrite_args


class _PipedInput(io.StringIO):
    def isatty(self) -> bool:
        return False


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


def test_build_initial_message_combines_stdin_files_and_prompt(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "example.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", _PipedInput("piped text"))

    result = _build_initial_message("Review this", (source,))

    assert result is not None
    assert result.startswith("piped text")
    assert f'<file path="{source}">' in result
    assert "print('ok')" in result
    assert result.endswith("Review this")
