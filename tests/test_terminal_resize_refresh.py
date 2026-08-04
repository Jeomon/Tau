"""Regression: Terminal.enter_raw_mode must re-read the terminal size.

exit_raw_mode() uninstalls our SIGWINCH handler, so any resize that happens
while we do not own the terminal (an external editor via ctrl+g, a pager, an
interactive git command — see TUI.suspended) is never delivered. Without a
refresh on the way back in, the cached width/height still describe the
pre-suspend window and the forced repaint on resume reflows the whole
transcript to a width the terminal no longer has.
"""

from __future__ import annotations

import signal
import sys

import pytest

from tau.tui.terminal import Terminal


class _FakeStdin:
    def fileno(self) -> int:
        return 0


@pytest.fixture
def _no_tty(monkeypatch):
    """Neutralize the real termios/tty/signal side effects of enter_raw_mode."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr("tau.tui.terminal.termios.tcgetattr", lambda fd: [])
    monkeypatch.setattr("tau.tui.terminal.termios.tcsetattr", lambda fd, when, attrs: None)
    monkeypatch.setattr("tau.tui.terminal.tty.setraw", lambda fd: None)
    monkeypatch.setattr("tau.tui.terminal.signal.signal", lambda sig, handler: signal.SIG_DFL)


def test_enter_raw_mode_refreshes_stale_size(monkeypatch, _no_tty):
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (80, 24)))
    term = Terminal()
    assert (term.width, term.height) == (80, 24)

    # Resized while suspended: no SIGWINCH reached us, so nothing updated.
    term.exit_raw_mode()
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (120, 40)))
    assert (term.width, term.height) == (80, 24)

    term.enter_raw_mode()
    assert (term.width, term.height) == (120, 40)


def test_enter_raw_mode_does_not_fire_resize_callbacks(monkeypatch, _no_tty):
    """The refresh must not notify: with no running loop _on_resize would call
    back inline, painting synchronously out of what is otherwise a signal path.
    Callers needing a repaint force one themselves (TUI.suspended)."""
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (80, 24)))
    term = Terminal()
    fired: list[None] = []
    term.on_resize(lambda: fired.append(None))

    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (120, 40)))
    term.enter_raw_mode()

    assert (term.width, term.height) == (120, 40)
    assert fired == []
