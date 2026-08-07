"""An exception that escapes into the event loop must reach the screen.

A callback scheduled on the loop that raises reaches no `except` in Tau.
asyncio hands it to the loop's exception handler, whose default logs it on the
`asyncio` logger, and `_redirect_logging_off_terminal` sends that to a file.

One session's log held 48 WARNING+ records. 42 were `Exception in callback
TUI._on_stdin_ready()` — every keystroke into a permission prompt raising, for
hours, with nothing on screen. Notably they were filed under the `asyncio`
logger, not `tau.*`, so surfacing Tau's own warnings would have missed all of
them: the signal is not who logged it but that an exception escaped at all.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from tau.modes.interactive.app import App


class _Layout:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    def add_message(self, message: Any) -> None:
        self.messages.append(message)


class _TUI:
    def __init__(self) -> None:
        self.renders = 0

    def request_render(self) -> None:
        self.renders += 1


def _app() -> App:
    app = App.__new__(App)
    app._layout = _Layout()
    app._tui = _TUI()
    return app


def _lines(app: App) -> list[str]:
    out: list[str] = []
    for message in app._layout.messages:
        for content in message.contents:
            out.extend(content.lines)
    return out


async def _boom() -> None:
    raise AttributeError("'SelectList' object has no attribute 'append_search'")


@pytest.mark.asyncio
async def test_an_escaped_exception_reaches_the_transcript() -> None:
    app = _app()
    app._surface_event_loop_errors()
    loop = asyncio.get_running_loop()

    loop.call_soon(lambda: (_ for _ in ()).throw(RuntimeError("callback blew up")))
    await asyncio.sleep(0.01)

    text = " ".join(_lines(app))
    assert "internal error" in text
    assert "RuntimeError: callback blew up" in text
    assert app._tui.renders == 1


@pytest.mark.asyncio
async def test_the_logged_failure_is_reported_verbatim() -> None:
    app = _app()
    app._surface_event_loop_errors()
    loop = asyncio.get_running_loop()

    loop.call_exception_handler(
        {
            "message": "Exception in callback TUI._on_stdin_ready()",
            "exception": AttributeError("'SelectList' object has no attribute 'append_search'"),
        }
    )

    text = " ".join(_lines(app))
    assert "AttributeError" in text
    assert "append_search" in text


@pytest.mark.asyncio
async def test_a_repeating_failure_is_reported_once() -> None:
    """35 identical records in one session; one line, not 35."""
    app = _app()
    app._surface_event_loop_errors()
    loop = asyncio.get_running_loop()

    for _ in range(35):
        loop.call_exception_handler(
            {"message": "in callback", "exception": AttributeError("no attribute 'append_search'")}
        )

    assert len(app._layout.messages) == 1


@pytest.mark.asyncio
async def test_distinct_failures_are_each_reported() -> None:
    app = _app()
    app._surface_event_loop_errors()
    loop = asyncio.get_running_loop()

    loop.call_exception_handler({"message": "a", "exception": ValueError("first")})
    loop.call_exception_handler({"message": "b", "exception": ValueError("second")})

    assert len(app._layout.messages) == 2


@pytest.mark.asyncio
async def test_the_previous_handler_still_runs() -> None:
    """The log file must keep the full traceback; the screen line is additional."""
    seen: list[dict] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: seen.append(context))

    app = _app()
    app._surface_event_loop_errors()
    loop.call_exception_handler({"message": "x", "exception": ValueError("boom")})

    assert len(seen) == 1
    assert seen[0]["message"] == "x"
    assert len(app._layout.messages) == 1


@pytest.mark.asyncio
async def test_a_context_without_an_exception_is_ignored() -> None:
    """asyncio also reports non-exception conditions; those are not errors."""
    app = _app()
    app._surface_event_loop_errors()
    loop = asyncio.get_running_loop()

    loop.call_exception_handler({"message": "socket.send() raised exception"})

    assert app._layout.messages == []


@pytest.mark.asyncio
async def test_a_failure_to_render_does_not_recurse() -> None:
    """The handler runs on the loop; raising inside it would re-enter itself."""
    app = _app()

    def _explode(_message: Any) -> None:
        raise RuntimeError("layout is gone")

    app._layout.add_message = _explode  # type: ignore[method-assign]
    app._surface_event_loop_errors()
    loop = asyncio.get_running_loop()

    loop.call_exception_handler({"message": "x", "exception": ValueError("boom")})


def test_outside_a_running_loop_it_is_a_no_op() -> None:
    app = _app()

    with contextlib.suppress(RuntimeError):
        app._surface_event_loop_errors()

    assert app._layout.messages == []
