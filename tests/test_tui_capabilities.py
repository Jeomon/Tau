"""Tests for tau/tui/capabilities.py — detect_capabilities, cell dimensions, is_image_line."""

from __future__ import annotations

import pytest

from tau.tui.terminal import (
    CellDimensions,
    TerminalCapabilities,
    detect_capabilities,
    get_capabilities,
    get_cell_dimensions,
    is_image_line,
    reset_capabilities_cache,
    set_cell_dimensions,
)


@pytest.fixture(autouse=True)
def _clean_caps_cache():
    reset_capabilities_cache()
    yield
    reset_capabilities_cache()


class TestTerminalCapabilitiesDataclass:
    def test_construction(self):
        caps = TerminalCapabilities(images="kitty", truecolor=True, hyperlinks=True)
        assert caps.images == "kitty"
        assert caps.truecolor is True
        assert caps.hyperlinks is True

    def test_images_none(self):
        caps = TerminalCapabilities(images=None, truecolor=False, hyperlinks=False)
        assert caps.images is None


class TestCellDimensions:
    def test_construction(self):
        d = CellDimensions(width_px=9, height_px=18)
        assert d.width_px == 9
        assert d.height_px == 18

    def test_get_and_set(self):
        d = CellDimensions(width_px=12, height_px=24)
        set_cell_dimensions(d)
        assert get_cell_dimensions() is d

    def test_default_dimensions(self):
        d = get_cell_dimensions()
        assert d.width_px > 0
        assert d.height_px > 0


class TestDetectCapabilities:
    def test_returns_terminal_capabilities(self, monkeypatch):
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
        monkeypatch.delenv("WEZTERM_PANE", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)
        monkeypatch.delenv("GHOSTTY_RESOURCES_DIR", raising=False)
        monkeypatch.delenv("TERMINAL_EMULATOR", raising=False)
        monkeypatch.delenv("COLORTERM", raising=False)
        caps = detect_capabilities()
        assert isinstance(caps, TerminalCapabilities)

    def test_kitty_window_id_gives_kitty_protocol(self, monkeypatch):
        monkeypatch.setenv("KITTY_WINDOW_ID", "1")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        caps = detect_capabilities()
        assert caps.images == "kitty"
        assert caps.truecolor is True

    def test_iterm_session_gives_iterm2_protocol(self, monkeypatch):
        monkeypatch.setenv("ITERM_SESSION_ID", "abc")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        caps = detect_capabilities()
        assert caps.images == "iterm2"

    def test_wezterm_gives_kitty_protocol(self, monkeypatch):
        monkeypatch.setenv("WEZTERM_PANE", "0")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        caps = detect_capabilities()
        assert caps.images == "kitty"

    def test_tmux_disables_images(self, monkeypatch):
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
        monkeypatch.delenv("TERM", raising=False)
        caps = detect_capabilities()
        assert caps.images is None

    def test_vscode_no_images(self, monkeypatch):
        monkeypatch.setenv("TERM_PROGRAM", "vscode")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
        monkeypatch.delenv("WEZTERM_PANE", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        caps = detect_capabilities()
        assert caps.images is None
        assert caps.truecolor is True

    def test_colorterm_truecolor(self, monkeypatch):
        monkeypatch.setenv("COLORTERM", "truecolor")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
        monkeypatch.delenv("WEZTERM_PANE", raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)
        monkeypatch.delenv("GHOSTTY_RESOURCES_DIR", raising=False)
        monkeypatch.delenv("TERMINAL_EMULATOR", raising=False)
        caps = detect_capabilities()
        assert caps.truecolor is True

    def test_ghostty_env_var_gives_kitty_protocol(self, monkeypatch):
        monkeypatch.setenv("GHOSTTY_RESOURCES_DIR", "/usr/share/ghostty")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
        monkeypatch.delenv("WEZTERM_PANE", raising=False)
        caps = detect_capabilities()
        assert caps.images == "kitty"


class TestGetCapabilities:
    def test_cached_after_first_call(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        c1 = get_capabilities()
        c2 = get_capabilities()
        assert c1 is c2

    def test_reset_clears_cache(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        c1 = get_capabilities()
        reset_capabilities_cache()
        c2 = get_capabilities()
        assert c1 is not c2


class TestIsImageLine:
    def test_kitty_escape_detected(self):
        assert is_image_line("\x1b_Ga=T;") is True

    def test_iterm2_escape_detected(self):
        assert is_image_line("\x1b]1337;File=...") is True

    def test_plain_text_not_image(self):
        assert is_image_line("Hello, world!") is False

    def test_empty_line_not_image(self):
        assert is_image_line("") is False
