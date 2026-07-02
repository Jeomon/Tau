"""Autocomplete pickers must only act on unmodified keys.

Regression: pickers matched on bare ``event.key`` ("up"/"down"/"enter"), so a
modified arrow such as ``alt+up`` (restore queued messages) was swallowed by an
open picker instead of falling through to the editor. Same for ``alt+enter``
(follow-up) and ``shift+enter`` (newline).
"""

from __future__ import annotations

from tau.tui.autocomplete import AutocompleteItem, AutocompleteManager
from tau.tui.input import KeyEvent


def _manager_with_active_ac_picker() -> AutocompleteManager:
    mgr = AutocompleteManager(max_visible=5, request_render=lambda: None)
    mgr._ac_picker.set_items([AutocompleteItem(label="alpha"), AutocompleteItem(label="beta")])
    assert mgr._ac_picker.active
    return mgr


def test_plain_up_down_navigate_active_picker() -> None:
    mgr = _manager_with_active_ac_picker()
    consumed, _ = mgr.handle_input(KeyEvent(key="up"), "", 0)
    assert consumed is True
    consumed, _ = mgr.handle_input(KeyEvent(key="down"), "", 0)
    assert consumed is True


def test_alt_up_falls_through_for_dequeue() -> None:
    mgr = _manager_with_active_ac_picker()
    consumed, new_text = mgr.handle_input(KeyEvent(key="up", alt=True, raw="\x1b[1;3A"), "", 0)
    assert consumed is False
    assert new_text is None


def test_alt_enter_falls_through_for_followup() -> None:
    mgr = _manager_with_active_ac_picker()
    consumed, _ = mgr.handle_input(KeyEvent(key="enter", alt=True), "", 0)
    assert consumed is False


def test_shift_enter_falls_through_for_newline() -> None:
    mgr = _manager_with_active_ac_picker()
    consumed, _ = mgr.handle_input(KeyEvent(key="enter", shift=True), "", 0)
    assert consumed is False


def test_plain_enter_accepts_completion() -> None:
    mgr = _manager_with_active_ac_picker()
    consumed, _ = mgr.handle_input(KeyEvent(key="enter"), "/", 1)
    assert consumed is True
