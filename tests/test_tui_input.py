"""Tests for tau/tui/input.py — key normalization, matching, and Key constants."""

from __future__ import annotations

from tau.tui.input import (
    BgColorEvent,
    Key,
    KeyEvent,
    MouseEvent,
    PasteEvent,
    matches_key,
    normalize_key_id,
)


class TestNormalizeKeyid:
    def test_simple_key(self):
        mods, base = normalize_key_id("up")
        assert mods == frozenset()
        assert base == "up"

    def test_ctrl_modifier(self):
        mods, base = normalize_key_id("ctrl+p")
        assert "ctrl" in mods
        assert base == "p"

    def test_ctrl_shift_order_independent(self):
        m1, b1 = normalize_key_id("ctrl+shift+x")
        m2, b2 = normalize_key_id("shift+ctrl+x")
        assert m1 == m2
        assert b1 == b2

    def test_alias_control_equals_ctrl(self):
        m1, _ = normalize_key_id("ctrl+a")
        m2, _ = normalize_key_id("control+a")
        assert m1 == m2

    def test_key_alias_escape(self):
        _, base = normalize_key_id("esc")
        assert base == "escape"

    def test_plus_as_base_key(self):
        mods, base = normalize_key_id("ctrl++")
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

    def test_black_is_dark(self):
        assert BgColorEvent(r=0, g=0, b=0).is_dark is True

    def test_white_is_not_dark(self):
        assert BgColorEvent(r=255, g=255, b=255).is_dark is False

    def test_green_heavy_luminance(self):
        # green contributes 0.7152 * g; at g=180 → luminance ≈ 128.7, just above boundary
        assert BgColorEvent(r=0, g=180, b=0).is_dark is False


class TestDecodeModifier:
    def test_no_modifier(self):
        from tau.tui.input import _decode_modifier

        shift, alt, ctrl, meta = _decode_modifier(1)
        assert (shift, alt, ctrl, meta) == (False, False, False, False)

    def test_shift_only(self):
        from tau.tui.input import _decode_modifier

        shift, alt, ctrl, meta = _decode_modifier(2)
        assert shift is True
        assert alt is False

    def test_ctrl_shift(self):
        from tau.tui.input import _decode_modifier

        shift, alt, ctrl, meta = _decode_modifier(6)  # bits: shift=1, ctrl=4 → val=5 → mod=6
        assert shift is True
        assert ctrl is True

    def test_all_modifiers(self):
        from tau.tui.input import _decode_modifier

        # shift(1) + alt(2) + ctrl(4) + meta(8) = 15, mod = 16
        shift, alt, ctrl, meta = _decode_modifier(16)
        assert all([shift, alt, ctrl, meta])


class TestIsComplete:
    def test_plain_char_is_complete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("a") is True

    def test_bare_esc_is_incomplete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1b") is None

    def test_esc_letter_is_complete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1ba") is True

    def test_csi_incomplete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1b[") is None

    def test_csi_complete_arrow(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1b[A") is True

    def test_osc_complete_bel(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1b]11;rgb:ffff/ffff/ffff\x07") is True

    def test_osc_incomplete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1b]11;rgb:ffff") is None

    def test_ss3_complete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1bOP") is True

    def test_ss3_incomplete(self):
        from tau.tui.input import _is_complete

        assert _is_complete("\x1bO") is None


class TestInputParser:
    def _parser(self):
        from tau.tui.input import InputParser

        return InputParser()

    def _key(self, raw: str) -> KeyEvent:
        events = self._parser().feed(raw)
        assert len(events) == 1
        assert isinstance(events[0], KeyEvent)
        return events[0]

    def test_plain_char(self):
        e = self._key("a")
        assert e.key == "a"
        assert e.char == "a"

    def test_uppercase_sets_shift(self):
        e = self._key("A")
        assert e.shift is True
        assert e.char == "A"

    def test_ctrl_c(self):
        e = self._key("\x03")
        assert e.key == "c"
        assert e.ctrl is True

    def test_enter(self):
        assert self._key("\r").key == "enter"

    def test_backspace(self):
        assert self._key("\x7f").key == "backspace"

    def test_escape(self):
        p = self._parser()
        events = p.feed("\x1b")
        assert events == []
        events = p.flush()
        assert len(events) == 1
        assert isinstance(events[0], KeyEvent)
        assert events[0].key == "escape"

    def test_arrow_up(self):
        assert self._key("\x1b[A").key == "up"

    def test_arrow_down(self):
        assert self._key("\x1b[B").key == "down"

    def test_shift_tab(self):
        e = self._key("\x1b[Z")
        assert e.key == "tab"
        assert e.shift is True

    def test_delete_tilde(self):
        assert self._key("\x1b[3~").key == "delete"

    def test_focus_in(self):
        from tau.tui.input import FocusEvent

        events = self._parser().feed("\x1b[I")
        assert isinstance(events[0], FocusEvent)
        assert events[0].focused is True

    def test_focus_out(self):
        from tau.tui.input import FocusEvent

        events = self._parser().feed("\x1b[O")
        assert isinstance(events[0], FocusEvent)
        assert events[0].focused is False

    def test_osc_background_color(self):
        from tau.tui.input import BgColorEvent

        events = self._parser().feed("\x1b]11;rgb:ffff/0000/8080\x07")
        assert isinstance(events[0], BgColorEvent)
        assert events[0].r == 255
        assert events[0].g == 0

    def test_bracketed_paste(self):
        from tau.tui.input import PasteEvent

        events = self._parser().feed("\x1b[200~hello world\x1b[201~")
        assert isinstance(events[0], PasteEvent)
        assert events[0].text == "hello world"

    def test_alt_char(self):
        e = self._key("\x1ba")
        assert e.alt is True
        assert e.key == "a"

    def test_ctrl_shift_arrow(self):
        e = self._key("\x1b[1;6A")
        assert e.key == "up"
        assert e.ctrl is True
        assert e.shift is True

    def test_kitty_simple(self):
        assert self._key("\x1b[97u").key == "a"

    def test_kitty_release(self):
        e = self._key("\x1b[97;1:3u")
        assert e.key == "a"
        assert e.released is True

    def test_mouse_sgr_press(self):
        from tau.tui.input import MouseEvent

        events = self._parser().feed("\x1b[<0;10;5M")
        assert isinstance(events[0], MouseEvent)
        assert events[0].pressed is True

    def test_repeated_chars_split_into_separate_events(self):
        # A single read can batch several auto-repeated bytes together (e.g.
        # holding Space). Each must become its own KeyEvent so key matching
        # works — otherwise key="   " never matches "space".
        events = self._parser().feed("   ")
        assert len(events) == 3
        assert all(isinstance(e, KeyEvent) and e.matches("space") for e in events)

    def test_typed_run_splits_per_character(self):
        events = self._parser().feed("abc")
        assert [e.key for e in events if isinstance(e, KeyEvent)] == ["a", "b", "c"]

    def test_multibyte_char_kept_as_single_event(self):
        events = self._parser().feed("\U0001f600")
        assert len(events) == 1
        assert isinstance(events[0], KeyEvent)
        assert events[0].char == "\U0001f600"

    def test_char_then_escape_sequence(self):
        events = self._parser().feed("a\x1b[A")
        assert [e.key for e in events if isinstance(e, KeyEvent)] == ["a", "up"]

    def test_batched_escape_sequences_are_split(self):
        events = self._parser().feed("\x1b[A\x1b[B")
        assert [e.key for e in events if isinstance(e, KeyEvent)] == ["up", "down"]

    def test_escape_sequence_then_text_is_split(self):
        events = self._parser().feed("\x1b[Aa")
        assert [e.key for e in events if isinstance(e, KeyEvent)] == ["up", "a"]

    def test_batched_paste_then_key_is_split(self):
        from tau.tui.input import PasteEvent

        events = self._parser().feed("\x1b[200~hello\x1b[201~a")
        assert len(events) == 2
        assert isinstance(events[0], PasteEvent)
        assert events[0].text == "hello"
        assert isinstance(events[1], KeyEvent)
        assert events[1].key == "a"

    def test_flush_empty(self):
        assert self._parser().flush() == []

    def test_flush_partial_escape(self):
        p = self._parser()
        p.feed("\x1b")
        events = p.flush()
        assert len(events) == 1
        assert isinstance(events[0], KeyEvent)
        assert events[0].key == "escape"


class TestKittyEventTypeArrows:
    """Regression: terminals with the Kitty 'report event types' flag (e.g.
    Ghostty) encode arrows as CSI 1 ; mod : event <letter>. The event-type
    sub-parameter must be parsed or the keypress is silently dropped.
    """

    def _events(self, raw: str) -> list[KeyEvent]:
        from tau.tui.input import InputParser

        return [e for e in InputParser().feed(raw) if isinstance(e, KeyEvent)]

    def test_unmodified_arrow_press_with_event_type(self):
        # Ghostty form for a plain Up press — previously dropped.
        e = self._events("\x1b[1;1:1A")
        assert len(e) == 1
        assert e[0].key == "up"
        assert e[0].released is False
        assert (e[0].ctrl, e[0].alt, e[0].shift, e[0].meta) == (False, False, False, False)

    def test_arrow_repeat_is_not_released(self):
        e = self._events("\x1b[1;1:2A")
        assert e[0].key == "up"
        assert e[0].repeat is True
        assert e[0].released is False  # repeats must still drive navigation

    def test_arrow_release_is_flagged(self):
        e = self._events("\x1b[1;1:3A")
        assert e[0].key == "up"
        assert e[0].released is True  # dropped downstream by TUI._dispatch

    def test_modified_arrow_with_event_type(self):
        e = self._events("\x1b[1;2:1A")
        assert e[0].key == "up"
        assert e[0].shift is True
        assert e[0].released is False

    def test_tilde_key_with_event_type(self):
        e = self._events("\x1b[3;1:1~")
        assert e[0].key == "delete"
        assert e[0].released is False

    def test_legacy_arrow_still_works(self):
        e = self._events("\x1b[A")
        assert e[0].key == "up"
        assert e[0].released is False
