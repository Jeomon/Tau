"""Tests for tau/tui/input.py — KeyEvent, _normalize_keyid, matches_key, Key constants."""
from __future__ import annotations

from tau.tui.input import (
    KeyEvent,
    _normalize_keyid,
    matches_key,
    Key,
    PasteEvent,
    MouseEvent,
    BgColorEvent,
)


class TestNormalizeKeyid:
    def test_simple_key(self):
        mods, base = _normalize_keyid("up")
        assert mods == frozenset()
        assert base == "up"

    def test_ctrl_modifier(self):
        mods, base = _normalize_keyid("ctrl+p")
        assert "ctrl" in mods
        assert base == "p"

    def test_ctrl_shift_order_independent(self):
        m1, b1 = _normalize_keyid("ctrl+shift+x")
        m2, b2 = _normalize_keyid("shift+ctrl+x")
        assert m1 == m2
        assert b1 == b2

    def test_alias_control_equals_ctrl(self):
        m1, _ = _normalize_keyid("ctrl+a")
        m2, _ = _normalize_keyid("control+a")
        assert m1 == m2

    def test_key_alias_escape(self):
        _, base = _normalize_keyid("esc")
        assert base == "escape"

    def test_plus_as_base_key(self):
        mods, base = _normalize_keyid("ctrl++")
        assert "ctrl" in mods
        assert base == "+"


class TestKeyEventMatches:
    def test_simple_key_matches(self):
        event = KeyEvent(key="up")
        assert event.matches("up") is True

    def test_does_not_match_different_key(self):
        event = KeyEvent(key="down")
        assert event.matches("up") is False

    def test_ctrl_matches(self):
        event = KeyEvent(key="p", ctrl=True)
        assert event.matches("ctrl+p") is True

    def test_ctrl_does_not_match_plain(self):
        event = KeyEvent(key="p", ctrl=True)
        assert event.matches("p") is False

    def test_modifier_order_independent(self):
        event = KeyEvent(key="x", ctrl=True, shift=True)
        assert event.matches("ctrl+shift+x") is True
        assert event.matches("shift+ctrl+x") is True

    def test_matches_any_of_multiple_combos(self):
        event = KeyEvent(key="n", ctrl=True)
        assert event.matches("up", "ctrl+n", "down") is True

    def test_matches_none_of_multiple_combos(self):
        event = KeyEvent(key="z")
        assert event.matches("up", "ctrl+p") is False

    def test_alt_matches(self):
        event = KeyEvent(key="enter", alt=True)
        assert event.matches("alt+enter") is True

    def test_escape_alias(self):
        event = KeyEvent(key="escape")
        assert event.matches("esc") is True

    def test_enter_matches(self):
        event = KeyEvent(key="enter")
        assert event.matches("enter") is True


class TestKeyEventStr:
    def test_plain_key(self):
        event = KeyEvent(key="x")
        assert str(event) == "x"

    def test_ctrl_key(self):
        event = KeyEvent(key="c", ctrl=True)
        assert "ctrl" in str(event)
        assert "c" in str(event)

    def test_ctrl_alt_key(self):
        event = KeyEvent(key="p", ctrl=True, alt=True)
        s = str(event)
        assert "ctrl" in s
        assert "alt" in s
        assert "p" in s


class TestMatchesKeyFunction:
    def test_key_event_matches(self):
        event = KeyEvent(key="enter")
        assert matches_key(event, "enter") is True

    def test_non_key_event_returns_false(self):
        paste = PasteEvent(text="hello")
        assert matches_key(paste, "enter") is False  # type: ignore[arg-type]

    def test_mouse_event_returns_false(self):
        mouse = MouseEvent(x=10, y=5, button=1, pressed=True)
        assert matches_key(mouse, "enter") is False  # type: ignore[arg-type]


class TestKeyConstants:
    def test_escape(self):
        assert Key.ESCAPE == "escape"

    def test_enter(self):
        assert Key.ENTER == "enter"

    def test_tab(self):
        assert Key.TAB == "tab"

    def test_up_down_left_right(self):
        assert Key.UP == "up"
        assert Key.DOWN == "down"
        assert Key.LEFT == "left"
        assert Key.RIGHT == "right"

    def test_f_keys(self):
        assert Key.F1 == "f1"
        assert Key.F12 == "f12"

    def test_page_up_down(self):
        assert Key.PAGE_UP == "pageup"
        assert Key.PAGE_DOWN == "pagedown"


class TestKeyModifierBuilders:
    def test_ctrl(self):
        assert Key.ctrl("c") == "ctrl+c"

    def test_alt(self):
        assert Key.alt("enter") == "alt+enter"

    def test_shift(self):
        assert Key.shift("tab") == "shift+tab"

    def test_meta(self):
        assert Key.meta("x") == "meta+x"

    def test_ctrl_shift(self):
        assert Key.ctrl_shift("p") == "ctrl+shift+p"

    def test_ctrl_alt(self):
        assert Key.ctrl_alt("d") == "ctrl+alt+d"

    def test_alt_shift(self):
        assert Key.alt_shift("s") == "alt+shift+s"

    def test_ctrl_shift_alt(self):
        assert Key.ctrl_shift_alt("q") == "ctrl+shift+alt+q"

    def test_key_constant_works_with_matches(self):
        event = KeyEvent(key="escape")
        assert event.matches(Key.ESCAPE) is True

    def test_ctrl_builder_works_with_matches(self):
        event = KeyEvent(key="c", ctrl=True)
        assert event.matches(Key.ctrl("c")) is True


class TestPasteEvent:
    def test_fields(self):
        p = PasteEvent(text="hello world", raw="\x1b[200~hello world\x1b[201~")
        assert p.text == "hello world"

    def test_default_raw(self):
        p = PasteEvent(text="hi")
        assert p.raw == ""


class TestMouseEvent:
    def test_fields(self):
        m = MouseEvent(x=5, y=10, button=1, pressed=True)
        assert m.x == 5
        assert m.y == 10
        assert m.button == 1
        assert m.pressed is True


class TestBgColorEvent:
    def test_dark_background(self):
        e = BgColorEvent(r=30, g=30, b=30)
        assert e.is_dark is True

    def test_light_background(self):
        e = BgColorEvent(r=240, g=240, b=240)
        assert e.is_dark is False

    def test_luminance_boundary(self):
        e = BgColorEvent(r=0, g=179, b=0)
        assert isinstance(e.is_dark, bool)
