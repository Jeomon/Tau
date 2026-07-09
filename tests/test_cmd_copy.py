"""Regression test for /copy skipping empty aborted assistant turns.

An aborted turn is saved with empty content (see tau/engine/service.py's
Abort handling). /copy must keep scanning backward for the last assistant
message that actually has text instead of reporting "nothing to copy" the
moment it hits that empty entry.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tau.message.types import AssistantMessage
from tau.modes.interactive.commands.context import CommandContext
from tau.modes.interactive.commands.misc import cmd_copy
from tau.session.manager import SessionManager


def _ctx(tmp_path, notify: list) -> tuple[CommandContext, SessionManager]:
    manager = SessionManager(cwd=tmp_path, session_dir=tmp_path / "sessions", persist=False)
    runtime = SimpleNamespace(session_manager=manager)
    layout = SimpleNamespace()
    tui = SimpleNamespace()
    ctx = CommandContext(runtime=runtime, layout=layout, tui=tui)
    ctx.notify = notify.append  # type: ignore[method-assign]
    return ctx, manager


def test_copy_skips_empty_aborted_turn_and_finds_earlier_text(tmp_path) -> None:
    notify: list[str] = []
    ctx, manager = _ctx(tmp_path, notify)

    manager.append_message(AssistantMessage.from_text("earlier real reply"))
    manager.append_message(AssistantMessage())  # aborted turn: no content

    with patch("tau.modes.interactive.commands.misc.copy_to_clipboard") as copy_mock:
        cmd_copy(ctx)

    copy_mock.assert_called_once_with("earlier real reply")
    assert notify == ["Copied last assistant message to clipboard."]


def test_copy_reports_nothing_when_only_aborted_turns_exist(tmp_path) -> None:
    notify: list[str] = []
    ctx, manager = _ctx(tmp_path, notify)

    manager.append_message(AssistantMessage())

    with patch("tau.modes.interactive.commands.misc.copy_to_clipboard") as copy_mock:
        cmd_copy(ctx)

    copy_mock.assert_not_called()
    assert notify == ["No assistant messages to copy."]
