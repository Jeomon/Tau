"""Resume selector session loading must not block the TUI event loop."""

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
        self.append_resume_sessions_calls: list[tuple[str, list, bool]] = []

    def open_resume_selector(self, **kwargs: Any) -> None:
        self.open_resume_selector_calls.append(kwargs)

    def append_resume_sessions(self, scope: str, sessions: list, has_more: bool) -> None:
        self.append_resume_sessions_calls.append((scope, sessions, has_more))


def _ctx(tmp_path) -> tuple[CommandContext, _FakeLayout]:
    runtime: Any = Runtime.__new__(Runtime)
    runtime._context = SimpleNamespace(
        session_manager=SimpleNamespace(cwd=tmp_path, session_file=None),
    )
    layout = _FakeLayout()
    ctx = CommandContext(
        runtime=runtime,
        layout=layout,  # type: ignore[arg-type]
        tui=SimpleNamespace(request_render=lambda: None),  # type: ignore[arg-type]
    )
    return ctx, layout


def test_open_resume_selector_loads_first_page_off_the_main_thread(tmp_path, monkeypatch) -> None:
    ctx, layout = _ctx(tmp_path)
    page_thread_name: str | None = None
    page_started = threading.Event()
    allow_page_to_finish = threading.Event()

    class _Pager:
        def next_page(self, _page_size: int) -> tuple[list, bool]:
            nonlocal page_thread_name
            page_thread_name = threading.current_thread().name
            page_started.set()
            assert allow_page_to_finish.wait(timeout=1)
            return [], False

    monkeypatch.setattr(SessionManager, "pager", staticmethod(lambda _cwd: _Pager()))

    async def run() -> None:
        await open_resume_selector(ctx)
        assert len(layout.open_resume_selector_calls) == 1
        assert layout.open_resume_selector_calls[0]["sessions"] == []
        assert layout.open_resume_selector_calls[0]["loading"] is True

        for _ in range(20):
            if page_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert page_started.is_set(), "first page was never loaded"
        assert layout.append_resume_sessions_calls == []

        allow_page_to_finish.set()
        for _ in range(20):
            if layout.append_resume_sessions_calls:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("background session page did not finish")

    asyncio.run(run())

    assert page_thread_name != threading.main_thread().name
    assert layout.append_resume_sessions_calls == [("current", [], False)]
