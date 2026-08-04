"""The render_backend flag, and the safety property that it defaults off.

The app-viewport backend captures the mouse. That disables the terminal's own
wheel scrolling *and* click-drag selection, so a leaked ``\\x1b[?1000h`` would
silently degrade every user who never opted in. These tests exist to make that
leak hard to ship, not to check that a settings field round-trips.
"""

from __future__ import annotations

import subprocess

import pytest

from tau.settings.manager import SettingsManager
from tau.settings.types import Settings


def _manager(render_backend: object) -> SettingsManager:
    """A SettingsManager exposing only what the getter reads.

    The real __init__ needs disk paths and a loaded config; the resolution
    logic under test touches nothing but ``self.settings``.
    """
    m = object.__new__(SettingsManager)
    m.settings = Settings(render_backend=render_backend)  # type: ignore[arg-type]
    return m


class TestDefaultsOff:
    def test_unset_resolves_to_native_scrollback(self) -> None:
        assert _manager(None).get_render_backend() == "native-scrollback"

    @pytest.mark.parametrize(
        "bogus",
        ["app_viewport", "appviewport", "APP-VIEWPORT", "", "native", "true", 1, [], {}],
    )
    def test_unrecognised_values_fall_back_rather_than_enabling_capture(self, bogus) -> None:
        """A typo must not take the mouse away from the user.

        Falling back beats raising here: settings are hand-edited, and a hard
        failure at startup would be worse than quietly using the safe renderer.
        """
        assert _manager(bogus).get_render_backend() == "native-scrollback"

    def test_explicit_native_scrollback_is_honoured(self) -> None:
        assert _manager("native-scrollback").get_render_backend() == "native-scrollback"

    def test_app_viewport_is_honoured_when_asked_for_exactly(self) -> None:
        assert _manager("app-viewport").get_render_backend() == "app-viewport"


class TestSetterValidation:
    def test_rejects_unknown_backends(self) -> None:
        m = object.__new__(SettingsManager)
        with pytest.raises(ValueError, match="render_backend must be one of"):
            SettingsManager.set_render_backend(m, "alt-screen")


def test_mouse_tracking_is_never_enabled_unguarded() -> None:
    """Every call to enable_mouse_tracking() must sit behind the flag.

    There are no call sites today — it has always been dead code. This guards
    the moment that changes: turning it on at startup, or anywhere the flag
    isn't consulted, breaks native selection for users who never opted in.
    """
    found = subprocess.run(
        ["git", "grep", "-n", r"\.enable_mouse_tracking(", "--", "tau/"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    for line in found:
        assert "app-viewport" in line or "render_backend" in line or "_app_viewport" in line, (
            f"enable_mouse_tracking() called without a render_backend guard:\n  {line}"
        )


class _FakeTerminal:
    def __init__(self, width: int = 80, height: int = 10) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []
        self.mouse_enabled = False
        self.resize_callbacks: list = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def enable_mouse_tracking(self) -> None:
        self.mouse_enabled = True
        self.writes.append("\x1b[?1000h\x1b[?1006h")

    def disable_mouse_tracking(self) -> None:
        self.mouse_enabled = False

    def on_resize(self, cb):
        self.resize_callbacks.append(cb)
        return lambda: self.resize_callbacks.remove(cb)


class TestTUIHonoursTheFlag:
    """End-to-end: the flag must decide whether the mouse is ever captured."""

    def _tui(self, backend: str):
        from tau.tui.component import StaticComponent
        from tau.tui.service import TUI

        term = _FakeTerminal()
        tui = TUI(terminal=term, render_backend=backend)  # type: ignore[arg-type]
        tui.children.append(StaticComponent(["content"]))
        return tui, term

    def test_default_backend_never_touches_the_mouse(self) -> None:
        tui, term = self._tui("native-scrollback")
        tui._do_render()
        assert not term.mouse_enabled
        assert "\x1b[?1000h" not in "".join(term.writes)
        assert tui._app_viewport is None
        tui.dispose()

    def test_app_viewport_captures_the_mouse_on_the_first_frame(self) -> None:
        tui, term = self._tui("app-viewport")
        assert not term.mouse_enabled, "constructing a TUI must not touch the terminal"

        tui._do_render()

        assert term.mouse_enabled
        tui.dispose()

    def test_dispose_hands_the_mouse_back(self) -> None:
        tui, term = self._tui("app-viewport")
        tui._do_render()
        tui.dispose()
        assert not term.mouse_enabled
