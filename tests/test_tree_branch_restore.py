"""Regression test for restoring a rewound user message into the editor.

/tree lets you jump back to a prior user message; the message text is put
back into the editor so you can re-send it. Branch navigation is async (it
can run a summarization LLM call), so if the user types something into the
editor while that's in flight, restoring the rewound text must not clobber
what they typed — mirrors pi's #1169 fix.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from tau.agent.types import AgentPhase
from tau.hooks.service import Hooks
from tau.message.types import AssistantMessage, UserMessage
from tau.modes.interactive.commands.context import CommandContext
from tau.modes.interactive.commands.session import _apply_tree_branch
from tau.runtime.service import Runtime
from tau.session.manager import SessionManager


class _Settings:
    def is_branch_summary_enabled(self) -> bool:
        return False

    def get_branch_summary_skip_prompt(self) -> bool:
        return True


class _FakeInput:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def set_text(self, text: str) -> None:
        self.text = text


class _FakeLayout:
    def __init__(self, editor_text: str = "") -> None:
        self.input = _FakeInput(editor_text)
        self.spinner = SimpleNamespace(set_label=lambda *a, **k: None, clear_label=lambda: None)

    def get_editor_text(self) -> str:
        return self.input.text

    def add_message(self, msg: object) -> None:
        pass

    def open_tree_selector(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("should not prompt when branch-summary is disabled")


def _ctx(tmp_path, editor_text: str = "") -> tuple[CommandContext, SessionManager, str]:
    manager = SessionManager(cwd=tmp_path, session_dir=tmp_path / "sessions", persist=False)
    user_entry_id = manager.append_message(UserMessage.from_text("rewound prompt"))
    manager.append_message(AssistantMessage.from_text("reply"))

    runtime: Any = Runtime.__new__(Runtime)
    runtime._context = SimpleNamespace(
        agent=SimpleNamespace(_phase=AgentPhase.IDLE),
        session_manager=manager,
        settings_manager=_Settings(),
        llm=None,
        hooks=Hooks(),
    )
    runtime._layout = None
    runtime._emit_session_start = AsyncMock()

    layout = _FakeLayout(editor_text)
    ctx = CommandContext(
        runtime=runtime, layout=layout, tui=SimpleNamespace(request_render=lambda: None)
    )
    return ctx, manager, user_entry_id


def test_restores_rewound_text_into_empty_editor(tmp_path) -> None:
    ctx, _manager, user_entry_id = _ctx(tmp_path, editor_text="")

    asyncio.run(_apply_tree_branch(ctx, user_entry_id))

    assert ctx.layout.input.text == "rewound prompt"


def test_does_not_clobber_text_already_typed_into_editor(tmp_path) -> None:
    ctx, _manager, user_entry_id = _ctx(tmp_path, editor_text="something I'm mid-typing")

    asyncio.run(_apply_tree_branch(ctx, user_entry_id))

    assert ctx.layout.input.text == "something I'm mid-typing"
