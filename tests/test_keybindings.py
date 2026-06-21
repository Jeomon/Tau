"""Tests for tau/tui/keybindings.py — KeybindingsManager, Key constants."""
from __future__ import annotations

import pytest

from tau.tui.keybindings import (
    KeybindingsManager,
    get_keybindings,
    configure_keybindings,
    _DEFAULTS,
)
from tau.tui.input import KeyEvent


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False, meta: bool = False) -> KeyEvent:
    return KeyEvent(key=key, ctrl=ctrl, alt=alt, shift=shift, meta=meta)


@pytest.fixture(autouse=True)
def _reset_singleton():
    import tau.tui.keybindings as kb
    original = kb._instance
    yield
    kb._instance = original


class TestKeybindingsManagerConstruction:
    def test_defaults_loaded(self):
        m = KeybindingsManager()
        assert "tui.select.up" in m._map

    def test_overrides_merged(self):
        m = KeybindingsManager(overrides={"tui.select.up": ["ctrl+k"]})
        assert m.keys_for("tui.select.up") == ["ctrl+k"]

    def test_non_overridden_defaults_retained(self):
        m = KeybindingsManager(overrides={"tui.select.up": ["ctrl+k"]})
        assert "enter" in m.keys_for("tui.select.confirm")

    def test_none_overrides_is_fine(self):
        m = KeybindingsManager(overrides=None)
        assert len(m._map) > 0


class TestKeybindingsManagerMatches:
    def test_matches_default_up(self):
        m = KeybindingsManager()
        event = _key("up")
        assert m.matches(event, "tui.select.up") is True

    def test_matches_ctrl_p_for_up(self):
        m = KeybindingsManager()
        event = _key("p", ctrl=True)
        assert m.matches(event, "tui.select.up") is True

    def test_does_not_match_wrong_action(self):
        m = KeybindingsManager()
        event = _key("up")
        assert m.matches(event, "tui.select.down") is False

    def test_matches_enter_for_confirm(self):
        m = KeybindingsManager()
        assert m.matches(_key("enter"), "tui.select.confirm") is True

    def test_matches_after_bind(self):
        m = KeybindingsManager()
        m.bind("tui.select.up", ["ctrl+k"])
        assert m.matches(_key("k", ctrl=True), "tui.select.up") is True
        assert m.matches(_key("up"), "tui.select.up") is False

    def test_unknown_action_no_match(self):
        m = KeybindingsManager()
        assert m.matches(_key("x"), "nonexistent.action") is False


class TestKeybindingsManagerKeysFor:
    def test_keys_for_known_action(self):
        m = KeybindingsManager()
        keys = m.keys_for("tui.select.up")
        assert "up" in keys
        assert "ctrl+p" in keys

    def test_keys_for_unknown_returns_empty(self):
        m = KeybindingsManager()
        assert m.keys_for("does.not.exist") == []

    def test_keys_for_returns_copy(self):
        m = KeybindingsManager()
        k1 = m.keys_for("tui.select.up")
        k1.append("extra")
        assert "extra" not in m.keys_for("tui.select.up")


class TestKeybindingsManagerBind:
    def test_bind_replaces_all(self):
        m = KeybindingsManager()
        m.bind("tui.select.up", ["ctrl+k"])
        assert m.keys_for("tui.select.up") == ["ctrl+k"]

    def test_bind_new_action(self):
        m = KeybindingsManager()
        m.bind("custom.action", ["ctrl+g"])
        assert m.keys_for("custom.action") == ["ctrl+g"]


class TestKeybindingsManagerAddBinding:
    def test_add_binding_appends(self):
        m = KeybindingsManager()
        original = m.keys_for("tui.select.up")[:]
        m.add_binding("tui.select.up", "ctrl+k")
        assert "ctrl+k" in m.keys_for("tui.select.up")
        assert all(k in m.keys_for("tui.select.up") for k in original)

    def test_add_binding_no_duplicate(self):
        m = KeybindingsManager()
        m.add_binding("tui.select.up", "up")
        assert m.keys_for("tui.select.up").count("up") == 1

    def test_add_binding_new_action(self):
        m = KeybindingsManager()
        m.add_binding("brand.new", "ctrl+z")
        assert "ctrl+z" in m.keys_for("brand.new")


class TestGlobalSingleton:
    def test_get_keybindings_returns_instance(self):
        kb = get_keybindings()
        assert isinstance(kb, KeybindingsManager)

    def test_get_keybindings_is_singleton(self):
        k1 = get_keybindings()
        k2 = get_keybindings()
        assert k1 is k2

    def test_configure_keybindings_replaces_singleton(self):
        configure_keybindings({"tui.select.up": ["ctrl+k"]})
        kb = get_keybindings()
        assert kb.keys_for("tui.select.up") == ["ctrl+k"]
