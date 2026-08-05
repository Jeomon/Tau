"""Behaviour ScrollbackRenderer inherited from ScrollbackTerminal, now tested here.

Two pieces were ported into the string renderer but had no coverage of their
own — they were only guarded by tests aimed at the cell renderer, which is
about to be deleted:

* the Termux height-only-resize exemption, which keeps diff state when the
  Android on-screen keyboard toggles (otherwise every keypress replays the
  whole transcript)
* novelty-tracked raw writes, which is how inline images avoid being resent
  whenever a neighbouring line changes

Written before deleting the cell path, so the new code is covered by its own
tests rather than inheriting confidence from the old ones.
"""

from __future__ import annotations

import pytest

from tau.tui.buffer import RawWrite
from tau.tui.scrollback import ScrollbackRenderer


class _Term:
    def __init__(self, width: int = 40, height: int = 10) -> None:
        self.width, self.height = width, height
        self.writes: list[str] = []
        self._cbs: list = []

    def write(self, s: str) -> None:
        self.writes.append(s)

    write_flush = write

    def flush(self) -> None:
        pass

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def on_resize(self, cb):
        self._cbs.append(cb)
        return lambda: None

    def fire_resize(self) -> None:
        for cb in list(self._cbs):
            cb()

    @property
    def text(self) -> str:
        return "".join(self.writes)


class TestTermuxHeightResize:
    """Termux reports a height change every time the keyboard shows or hides.

    Treating that as a real resize costs a full clear plus a replay of the
    entire transcript on every keyboard toggle — which is most of what typing
    on Android is. Width is untouched, so the diff stays valid.
    """

    def _rendered(self, monkeypatch, *, termux: bool):
        monkeypatch.setattr("tau.tui.scrollback._IS_TERMUX", termux)
        term = _Term(width=40, height=10)
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        r.render([f"line {i}" for i in range(20)])
        return term, r

    def test_height_only_change_keeps_diff_state(self, monkeypatch) -> None:
        term, r = self._rendered(monkeypatch, termux=True)
        assert r._prev is not None
        term.height = 6
        term.fire_resize()
        # diff state survives: no full redraw is armed
        assert r._prev is not None
        assert r._resized is False

    def test_height_only_change_reanchors_the_viewport(self, monkeypatch) -> None:
        term, r = self._rendered(monkeypatch, termux=True)
        term.height = 6
        term.fire_resize()
        # content is 20 rows; the top visible row must follow the new height
        assert r._viewport_top == 20 - 6

    def test_width_change_still_forces_a_full_redraw(self, monkeypatch) -> None:
        term, r = self._rendered(monkeypatch, termux=True)
        term.width = 30
        term.fire_resize()
        assert r._prev is None
        assert r._resized is True

    def test_height_change_off_termux_forces_a_full_redraw(self, monkeypatch) -> None:
        term, r = self._rendered(monkeypatch, termux=False)
        term.height = 6
        term.fire_resize()
        assert r._prev is None
        assert r._resized is True


class TestRawWrites:
    """Inline images bypass the text diff and carry their own novelty check."""

    def _raw(self, x: int, y: int, data: str, token: str) -> RawWrite:
        return RawWrite(x=x, y=y, data=data, token=token)

    def test_sent_on_first_render(self) -> None:
        term = _Term()
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        r.render(["a", "b"], raw_writes=[self._raw(0, 1, "IMG", "t1")])
        assert "IMG" in term.text

    def test_sent_even_when_no_text_row_changed(self) -> None:
        """An image row carries no printable text, so the line diff never flags it."""
        term = _Term()
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        r.render(["a", "b"])
        before = term.text
        r.render(["a", "b"], raw_writes=[self._raw(0, 1, "IMG", "t1")])
        assert "IMG" in term.text[len(before) :]

    def test_unchanged_token_is_not_resent(self) -> None:
        """Resending a multi-MB payload because a neighbour moved would be costly."""
        term = _Term()
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        rw = [self._raw(0, 1, "IMG", "t1")]
        r.render(["a", "b"], raw_writes=rw)
        before = term.text
        r.render(["a", "changed"], raw_writes=rw)
        assert "IMG" not in term.text[len(before) :]

    def test_changed_token_at_the_same_position_resends(self) -> None:
        term = _Term()
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        r.render(["a", "b"], raw_writes=[self._raw(0, 1, "IMG1", "t1")])
        before = term.text
        r.render(["a", "b"], raw_writes=[self._raw(0, 1, "IMG2", "t2")])
        assert "IMG2" in term.text[len(before) :]

    def test_clear_forces_a_resend(self) -> None:
        """The screen was erased, so whatever was drawn there is gone with it."""
        term = _Term()
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        rw = [self._raw(0, 1, "IMG", "t1")]
        r.render(["a", "b"], raw_writes=rw)
        r.clear()
        before = term.text
        r.render(["a", "b"], raw_writes=rw)
        assert "IMG" in term.text[len(before) :]

    def test_width_change_forces_a_resend(self) -> None:
        term = _Term()
        r = ScrollbackRenderer(term)  # type: ignore[arg-type]
        rw = [self._raw(0, 1, "IMG", "t1")]
        r.render(["a", "b"], raw_writes=rw)
        term.width = 30
        term.fire_resize()
        before = term.text
        r.render(["a", "b"], raw_writes=rw)
        assert "IMG" in term.text[len(before) :]


def test_no_raw_writes_is_a_noop() -> None:
    term = _Term()
    r = ScrollbackRenderer(term)  # type: ignore[arg-type]
    r.render(["a"], raw_writes=[])
    r.render(["a"], raw_writes=None)


@pytest.mark.parametrize("rows", [0, 1, 50])
def test_renders_any_row_count(rows: int) -> None:
    term = _Term()
    r = ScrollbackRenderer(term)  # type: ignore[arg-type]
    r.render([f"r{i}" for i in range(rows)])
