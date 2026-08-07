"""Extension failures must reach the screen in interactive mode.

RPC has forwarded these to its client since it existed. Interactive mode never
wired the callback, so ``ExtensionRuntime._invoke_handler`` recorded the error,
logged a warning, and that was the end of it — ``_redirect_logging_off_terminal``
sends that logger to a file precisely so nothing corrupts the renderer.

The reason this matters beyond tidiness: a raising ``tool_call`` handler is read
by the host as "no objection", so a permission gate that crashes mid-decision
turns into an allowed tool call. Silent was the wrong default for that.
"""

from __future__ import annotations

from typing import Any

from tau.modes.interactive.app import App


class _Error:
    """Shape of ``ExtensionError`` as the callback consumes it."""

    def __init__(self, path: str, event: str, error: str) -> None:
        self.extension_path = path
        self.event = event
        self.error = error


class _Layout:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    def add_message(self, message: Any) -> None:
        self.messages.append(message)


class _Tui:
    def __init__(self) -> None:
        self.renders = 0

    def request_render(self) -> None:
        self.renders += 1


class _Runtime:
    def __init__(self) -> None:
        self.callback: Any = None

    def set_extension_error_callback(self, callback: Any) -> None:
        self.callback = callback


def _app() -> tuple[Any, _Runtime, _Layout, _Tui]:
    app = App.__new__(App)
    runtime, layout, tui = _Runtime(), _Layout(), _Tui()
    app._runtime = runtime  # type: ignore[attr-defined]
    app._layout = layout  # type: ignore[attr-defined]
    app._tui = tui  # type: ignore[attr-defined]
    app._surface_extension_errors()
    return app, runtime, layout, tui


def _lines(message: Any) -> list[str]:
    return list(message.contents[0].lines)


def test_the_callback_is_registered() -> None:
    _, runtime, _, _ = _app()

    assert callable(runtime.callback), "interactive mode never wired this before"


def test_an_error_reaches_the_message_list() -> None:
    _, runtime, layout, tui = _app()

    runtime.callback(_Error("/x/permissions/__init__.py", "tool_call", "RuntimeError: boom"))

    assert len(layout.messages) == 1
    text = "\n".join(_lines(layout.messages[0]))
    assert "permissions" in text, "the failing extension is named"
    assert "tool_call" in text, "the event is named"
    assert "RuntimeError: boom" in text
    assert tui.renders == 1, "a queued message nobody paints is still invisible"


def test_it_is_styled_as_an_error() -> None:
    _, runtime, layout, _ = _app()

    runtime.callback(_Error("/x/ext/__init__.py", "tool_call", "boom"))

    assert layout.messages[0].contents[0].notify_type == "error"


def test_repeats_are_suppressed() -> None:
    """A gate that raises does it on every call; one notice, not fifty."""
    _, runtime, layout, _ = _app()
    error = _Error("/x/ext/__init__.py", "tool_call", "boom")

    for _ in range(5):
        runtime.callback(error)

    assert len(layout.messages) == 1


def test_distinct_failures_are_all_reported() -> None:
    _, runtime, layout, _ = _app()

    runtime.callback(_Error("/x/a/__init__.py", "tool_call", "boom"))
    runtime.callback(_Error("/x/b/__init__.py", "tool_call", "boom"))
    runtime.callback(_Error("/x/a/__init__.py", "session_start", "boom"))
    runtime.callback(_Error("/x/a/__init__.py", "tool_call", "different"))

    assert len(layout.messages) == 4


def test_a_malformed_error_object_still_reports_something() -> None:
    """The callback is on the failure path; it must not add a second failure."""
    _, runtime, layout, _ = _app()

    runtime.callback(object())

    assert len(layout.messages) == 1
    assert "?" in "\n".join(_lines(layout.messages[0]))


def test_a_runtime_without_the_hook_is_tolerated() -> None:
    """Embedders can pass any object as the runtime."""
    app = App.__new__(App)
    app._runtime = object()  # type: ignore[attr-defined]

    app._surface_extension_errors()  # must not raise
