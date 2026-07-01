from types import SimpleNamespace

import pytest

from tau.modes.interactive.app import App


def _app(*, idle: bool = True) -> tuple[App, list[str]]:
    events: list[str] = []
    app = object.__new__(App)
    app._last_escape = 0.0
    app._runtime = SimpleNamespace(
        agent=SimpleNamespace(is_idle=lambda: idle),
        settings_manager=None,
    )
    app._input = SimpleNamespace(escape_abort=lambda: events.append("abort"))
    app._layout = SimpleNamespace(clear_messages=lambda: events.append("clear"))
    app._tui = SimpleNamespace(request_render=lambda: events.append("render"))
    return app, events


def test_single_escape_aborts_active_operation() -> None:
    app, events = _app(idle=False)

    app._handle_escape()

    assert events == ["abort"]


def test_double_escape_clears_messages_while_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    app, events = _app()
    times = iter((1.0, 1.2))
    monkeypatch.setattr("time.monotonic", lambda: next(times))

    app._handle_escape()
    assert events == []

    app._handle_escape()
    assert events == ["clear", "render"]
