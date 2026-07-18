"""Regression test: open_resume_selector() must not block the event loop
while listing sessions.

SessionManager.list() (via list_sessions_from_dir()/build_session_info())
reads and fully JSON-parses every session file in the project's session
directory — message counts need an exact tally, so it can't stop early.
This runs on every /resume, not just once, and a project accumulates
session files forever with nothing to prune them, so the cost only grows
the longer a project's been in use. Must run off the event loop thread so
opening the resume picker doesn't freeze the whole TUI while it scans.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

from tau.modes.interactive.commands.context import CommandContext
from tau.modes.interactive.commands.session import open_resume_selector
from tau.runtime.service import Runtime
from tau.session.manager import SessionManager


class _FakeLayout:
    def __init__(self) -> None:
        self.open_resume_selector_calls: list[dict] = []

    def open_resume_selector(self, **kwargs: Any) -> None:
        self.open_resume_selector_calls.append(kwargs)


def _ctx(tmp_path) -> tuple[CommandContext, _FakeLayout]:
    runtime: Any = Runtime.__new__(Runtime)
    runtime._context = SimpleNamespace(
        session_manager=SimpleNamespace(cwd=tmp_path, session_file=None),
    )
    layout = _FakeLayout()
    ctx = CommandContext(
        runtime=runtime, layout=layout, tui=SimpleNamespace(request_render=lambda: None)
    )
    return ctx, layout


def test_open_resume_selector_lists_sessions_off_the_main_thread(
    tmp_path, monkeypatch
) -> None:
    ctx, layout = _ctx(tmp_path)

    list_thread_name: str | None = None
    real_list = SessionManager.list

    def _spy_list(cwd, *args, **kwargs):
        nonlocal list_thread_name
        list_thread_name = threading.current_thread().name
        return real_list(cwd, *args, **kwargs)

    monkeypatch.setattr(SessionManager, "list", staticmethod(_spy_list))

    asyncio.run(open_resume_selector(ctx))

    assert list_thread_name is not None, "SessionManager.list() was never called"
    assert list_thread_name != threading.main_thread().name
    assert len(layout.open_resume_selector_calls) == 1
    assert layout.open_resume_selector_calls[0]["sessions"] == []
