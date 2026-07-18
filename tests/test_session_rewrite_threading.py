"""Regression tests: session-rewriting operations triggered from common
interactive actions must not block the event loop.

SessionManager._rewrite_file() serializes the whole session to disk
synchronously (measured: ~49ms for a 2000-turn session) whenever it runs —
not just once per session, but every time remove_last_message() is called
on an already-flushed session (the normal case) or create_branched_session()
runs. Both are reachable from ordinary interactive actions:
  - clone_session(): the /clone command.
  - Agent._on_message_rollback(): fired on every abort of an in-flight tool
    call — a routine interactive action, not a rare one.
Neither is exotic or infrequent enough to hand-wave away; both must run off
the main thread.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

from tau.agent.service import Agent
from tau.hooks.engine import MessageRollbackEvent
from tau.hooks.service import Hooks
from tau.message.types import AssistantMessage, UserMessage
from tau.runtime.service import Runtime
from tau.session.manager import SessionManager


def _make_session(tmp_path, n_turns: int = 50) -> SessionManager:
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path / "sessions", persist=True)
    for i in range(n_turns):
        sm.append_message(UserMessage.from_text(f"q {i}"))
        sm.append_message(AssistantMessage.from_text(f"a {i}"))
    return sm


def test_on_message_rollback_removes_messages_off_the_main_thread(tmp_path) -> None:
    sm = _make_session(tmp_path)
    entries_before = len(sm.entries)

    agent: Any = Agent.__new__(Agent)
    agent._session_manager = sm

    remove_thread_name: str | None = None
    real_remove = SessionManager.remove_last_message

    def _spy_remove(self, *args, **kwargs):
        nonlocal remove_thread_name
        remove_thread_name = threading.current_thread().name
        return real_remove(self, *args, **kwargs)

    sm.remove_last_message = _spy_remove.__get__(sm, SessionManager)  # type: ignore[method-assign]

    asyncio.run(agent._on_message_rollback(MessageRollbackEvent(count=2)))

    assert remove_thread_name is not None, "remove_last_message() was never called"
    assert remove_thread_name != threading.main_thread().name
    assert len(sm.entries) == entries_before - 2


def test_clone_session_creates_branch_off_the_main_thread(tmp_path) -> None:
    sm = _make_session(tmp_path)
    leaf_id = sm.get_leaf_id()

    create_thread_name: str | None = None
    real_create = SessionManager.create_branched_session

    def _spy_create(self, *args, **kwargs):
        nonlocal create_thread_name
        create_thread_name = threading.current_thread().name
        return real_create(self, *args, **kwargs)

    sm.create_branched_session = _spy_create.__get__(sm, SessionManager)  # type: ignore[method-assign]

    runtime: Any = Runtime.__new__(Runtime)
    runtime._context = SimpleNamespace(
        session_manager=sm,
        agent=None,
        ext_runtime=None,
        hooks=Hooks(),
    )
    runtime._extension_generation = 0
    runtime._emit_session_shutdown = _noop_async
    runtime._emit_session_start = _noop_async

    asyncio.run(runtime.clone_session())

    assert create_thread_name is not None, "create_branched_session() was never called"
    assert create_thread_name != threading.main_thread().name
    assert sm.session_file is not None
    assert leaf_id in sm.by_id


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    return None
