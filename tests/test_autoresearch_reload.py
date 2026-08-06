"""Regression tests: the autoresearch dashboard must survive a reload cleanly.

`Session.hide()` only removes the widget when `self._shown` is true. A reload
re-runs `register()` and builds a replacement `Session` whose `_shown` starts
false, so the replacement can never take down the widget its predecessor put on
screen. Unless the *outgoing* Session cleans up on `extension_unload`, the
dashboard is stranded there with nothing able to remove it.

The matching `extension_reloaded` subscription rebinds the new Session, since
`tui_ready` fires once per TUI rather than once per reload.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from tests.ext_loader import load_extension

_PKG = load_extension("autoresearch").__name__
autoresearch = importlib.import_module(_PKG)

WIDGET_KEY = autoresearch.WIDGET_KEY


class _UI:
    """Records widget add/remove so the lifecycle can be asserted directly."""

    supports_components = True

    def __init__(self) -> None:
        from tau.tui.theme import LayoutTheme

        # A real theme, not a stub: the dashboard chains style attributes
        # (theme.muted.dim() and friends), which a naive double cannot satisfy.
        self.theme = LayoutTheme()
        self.widgets: dict[str, Any] = {}
        self.calls: list[str] = []

    def set_widget(self, key: str, widget: Any, placement: str = "") -> None:
        self.widgets[key] = widget
        self.calls.append(f"set:{key}")

    def remove_widget(self, key: str) -> None:
        self.widgets.pop(key, None)
        self.calls.append(f"remove:{key}")

    def request_render(self) -> None:
        pass

    def notify(self, *_a: Any, **_k: Any) -> None:
        pass


class _Ctx:
    def __init__(self, ui: _UI) -> None:
        self.ui = ui
        self.has_ui = True


class _API:
    """ExtensionAPI double covering only what autoresearch's register() calls."""

    tui = None

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.tools: list[Any] = []
        self.commands: list[str] = []
        self.shortcuts: list[str] = []
        self.services: dict[str, Any] = {}

    def register_shortcut(self, key: str, description: str = "", handler: Any = None) -> Any:
        self.shortcuts.append(key)
        if handler is None:
            return lambda fn: fn
        return handler

    def provide(self, name: str, service: Any) -> None:
        self.services[name] = service

    def on(self, event: str, handler: Any = None) -> Any:
        if handler is None:

            def deco(fn: Any) -> Any:
                self.handlers.setdefault(event, []).append(fn)
                return fn

            return deco
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def register_tool(self, tool: Any) -> None:
        self.tools.append(tool)

    def register_command(self, name: str, *_a: Any, **_k: Any) -> None:
        self.commands.append(name)


def _register(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> _API:
    monkeypatch.chdir(cwd)
    api = _API()
    autoresearch.register(api)
    return api


def _fire(api: _API, event: str, ctx: _Ctx) -> None:
    for handler in api.handlers.get(event, []):
        handler(None, ctx)


def test_both_reload_events_are_subscribed(monkeypatch, tmp_path: Path) -> None:
    api = _register(monkeypatch, tmp_path)
    assert "extension_unload" in api.handlers, "outgoing Session must drop its widget"
    assert "extension_reloaded" in api.handlers, "incoming Session must rebind"


def test_the_widget_is_removed_when_the_extension_unloads(monkeypatch, tmp_path: Path) -> None:
    _activate(tmp_path)
    api = _register(monkeypatch, tmp_path)
    ui = _UI()
    ctx = _Ctx(ui)

    _fire(api, "tui_ready", ctx)
    assert WIDGET_KEY in ui.widgets, "precondition: dashboard is on screen"

    _fire(api, "extension_unload", ctx)

    assert WIDGET_KEY not in ui.widgets, "the dashboard outlived its own extension"
    assert f"remove:{WIDGET_KEY}" in ui.calls


def test_a_reload_rebinds_without_stranding_the_old_widget(monkeypatch, tmp_path: Path) -> None:
    _activate(tmp_path)
    ui = _UI()
    ctx = _Ctx(ui)

    old = _register(monkeypatch, tmp_path)
    _fire(old, "tui_ready", ctx)
    assert WIDGET_KEY in ui.widgets

    # Reload: the outgoing extension unloads, then a fresh one registers.
    _fire(old, "extension_unload", ctx)
    new = _register(monkeypatch, tmp_path)
    _fire(new, "extension_reloaded", ctx)

    # Exactly one widget, owned by the new Session — which can now remove it.
    assert WIDGET_KEY in ui.widgets
    _fire(new, "extension_unload", ctx)
    assert WIDGET_KEY not in ui.widgets, "the replacement Session cannot clean up"


def test_reload_is_a_no_op_when_there_is_nothing_to_show(monkeypatch, tmp_path: Path) -> None:
    api = _register(monkeypatch, tmp_path)
    ctx = _Ctx(_UI())
    _fire(api, "extension_reloaded", ctx)  # must not raise
    assert WIDGET_KEY not in ctx.ui.widgets


def test_unload_before_any_ui_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    api = _register(monkeypatch, tmp_path)
    _fire(api, "extension_unload", _Ctx(_UI()))


# ── helpers ──────────────────────────────────────────────────────────────────


def _session_of(api: _API) -> Any:
    """Reach the Session that register() closed over."""
    for handler in api.handlers["settled"]:
        for cell in handler.__closure__ or ():
            if isinstance(cell.cell_contents, autoresearch.Session):
                return cell.cell_contents
    raise AssertionError("no Session found in the registered handlers")


def _activate(cwd: Path) -> None:
    """Make the dashboard visible the way a real session does.

    ``Session.active`` is true once the prompt file exists, so writing it
    exercises the real code path instead of fabricating result objects.
    """
    state = importlib.import_module(f"{_PKG}.state")
    path = state.prompt_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# goal\noptimise something\n", encoding="utf-8")
