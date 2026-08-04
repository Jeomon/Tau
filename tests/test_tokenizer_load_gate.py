"""The tokenizer vocabulary load must not compete with the first frame.

Loading cl100k_base decodes ~100k base64 tokens (~80ms of pure CPU). The
footer's context-usage readout asks for a token count during ``tui_ready``,
which used to start that load in a thread that then fought the first paint for
the GIL — a straight addition to time-to-first-frame, on an empty screen.
Interactive startup now defers the load until the first frame is up.
"""

from __future__ import annotations

import asyncio

import pytest

from tau.session import compaction
from tau.tui.service import TUI


@pytest.fixture
def gate_state():
    """Save/restore the module-level load state mutated by these tests.

    conftest's session-scoped fixture has already completed a real load, so
    every global here is 'loaded' on entry and must be put back exactly —
    otherwise later tests silently fall back to the chars/4 estimate.
    """
    saved = (
        compaction._encoding_load_allowed,
        compaction._encoding_load_started,
        compaction._encoding,
    )
    yield
    (
        compaction._encoding_load_allowed,
        compaction._encoding_load_started,
        compaction._encoding,
    ) = saved


def test_defer_suppresses_the_load(gate_state, monkeypatch):
    started = []
    monkeypatch.setattr(compaction.threading, "Thread", lambda **kw: _FakeThread(started, **kw))
    compaction._encoding_load_started = False

    compaction.defer_encoding_load()
    compaction._start_loading_encoding()

    assert started == []


def test_deferred_call_does_not_consume_the_one_shot(gate_state, monkeypatch):
    """A call made while deferred must not count as 'the load already ran'.

    _start_loading_encoding is one-shot via _encoding_load_started; if the gate
    were checked after that flag was set, the suppressed call would latch it and
    the real load would never happen.
    """
    started = []
    monkeypatch.setattr(compaction.threading, "Thread", lambda **kw: _FakeThread(started, **kw))
    compaction._encoding_load_started = False

    compaction.defer_encoding_load()
    compaction._start_loading_encoding()  # suppressed
    compaction.allow_encoding_load()  # must still start it

    assert len(started) == 1


def test_allow_is_idempotent(gate_state, monkeypatch):
    started = []
    monkeypatch.setattr(compaction.threading, "Thread", lambda **kw: _FakeThread(started, **kw))
    compaction._encoding_load_started = False

    compaction.allow_encoding_load()
    compaction.allow_encoding_load()

    assert len(started) == 1


def test_undeferred_startup_is_unaffected(gate_state, monkeypatch):
    """Non-interactive modes never defer, so nothing changes for them."""
    started = []
    monkeypatch.setattr(compaction.threading, "Thread", lambda **kw: _FakeThread(started, **kw))
    compaction._encoding_load_started = False
    compaction._encoding_load_allowed = True

    compaction._start_loading_encoding()

    assert len(started) == 1


class _FakeThread:
    def __init__(self, log, target=None, name=None, daemon=None):
        self._target = target
        log.append(name)

    def start(self):
        pass


# ---------------------------------------------------------------------------
# The signal the release waits on
# ---------------------------------------------------------------------------


class FakeTerminal:
    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback):
        return lambda: None

    def __getattr__(self, name):
        return lambda *a, **k: ""


def test_first_render_signal_starts_unset_and_fires_once_painted():
    async def body() -> None:
        tui = TUI(terminal=FakeTerminal())  # type: ignore[arg-type]
        assert not tui._first_render_done.is_set()

        tui._do_render()

        await asyncio.wait_for(tui.wait_first_render(), 1.0)

    asyncio.run(body())


def test_first_render_signal_fires_even_if_render_raises(monkeypatch):
    """A renderer that crashes must not strand deferred work forever."""

    async def body() -> None:
        tui = TUI(terminal=FakeTerminal())  # type: ignore[arg-type]
        monkeypatch.setattr(
            tui._renderer, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        tui._do_render()

        await asyncio.wait_for(tui.wait_first_render(), 1.0)

    asyncio.run(body())
