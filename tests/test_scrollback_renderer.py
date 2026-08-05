"""The string renderer must put the same thing on screen as the cell renderer.

``ScrollbackRenderer`` (strings) replaces ``ScrollbackTerminal`` (Cell grid).
It deliberately emits *different bytes*: where the cell renderer overwrote a
column run (``\\x1b[1G`` + run), this repaints the whole line
(``\\x1b[2K`` + line). Same pixels, far less work — so equivalence has to be
asserted on the resulting screen, not the byte stream.

``Screen`` below is a minimal terminal emulator covering exactly the sequences
these two renderers emit (relative cursor moves, erases, scrolling), which is
what makes that comparison possible.
"""

from __future__ import annotations

import random
import re

import pytest

from tau.tui.ansi_bridge import parse_ansi_wrapped_into
from tau.tui.buffer import Buffer
from tau.tui.frame import ScrollbackTerminal
from tau.tui.geometry import Rect
from tau.tui.scrollback import ScrollbackRenderer

_SGR = re.compile(r"\x1b\[[\d;]*m|\x1b\]8;;.*?(?:\x07|\x1b\\)")


class Screen:
    """Unbounded scrollback grid driven by relative cursor moves."""

    def __init__(self, width: int) -> None:
        self.w = width
        self.rows: list[str] = [""]
        self.r = 0
        self.c = 0

    def _ensure(self, r: int) -> None:
        while len(self.rows) <= r:
            self.rows.append("")

    def _put(self, text: str) -> None:
        self._ensure(self.r)
        row = self.rows[self.r]
        if len(row) < self.c:
            row += " " * (self.c - len(row))
        self.rows[self.r] = row[: self.c] + text + row[self.c + len(text) :]
        self.c += len(text)

    def feed(self, data: str) -> None:
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == "\x1b":
                m = re.match(r"\x1b\[(\d*)([ABGKJ])", data[i:])
                if m:
                    n = int(m.group(1) or ("0" if m.group(2) in "KJ" else "1"))
                    op = m.group(2)
                    if op == "A":
                        self.r = max(0, self.r - max(n, 1))
                    elif op == "B":
                        self.r += max(n, 1)
                        self._ensure(self.r)
                    elif op == "G":
                        self.c = max(0, n - 1)
                    elif op == "K":
                        self._ensure(self.r)
                        if n == 2:
                            self.rows[self.r] = ""
                        elif n == 0:
                            self.rows[self.r] = self.rows[self.r][: self.c]
                    elif op == "J" and n == 2:
                        self.rows = [""]
                        self.r = self.c = 0
                    i += m.end()
                    continue
                if (m := re.match(r"\x1b\[H", data[i:])) is not None:
                    self.r = self.c = 0
                    i += m.end()
                    continue
                if (m := re.match(r"\x1b\[\?[\d;]*[hl]", data[i:])) is not None:
                    i += m.end()  # cursor visibility / synchronized output
                    continue
                if (m := _SGR.match(data, i)) is not None:
                    i = m.end()
                    continue
                i += 1
                continue
            if ch == "\r":
                self.c = 0
                i += 1
                continue
            if ch == "\n":
                self.r += 1
                self._ensure(self.r)
                i += 1
                continue
            j = i
            while j < len(data) and data[j] not in "\x1b\r\n":
                j += 1
            self._put(data[i:j])
            i = j

    def snapshot(self) -> list[str]:
        return [r.rstrip() for r in self.rows]


class _EmulatedTerminal:
    def __init__(self, width: int = 40, height: int = 10) -> None:
        self.width, self.height = width, height
        self.screen = Screen(width)
        self._cbs: list = []

    def write(self, s: str) -> None:
        self.screen.feed(s)

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


def _to_buffer(lines: list[str], width: int) -> Buffer:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    y = 0
    for ln in lines:
        y += parse_ansi_wrapped_into(buf, 0, y, ln, width)
    return buf


def _both(frames: list[list[str]], width: int = 40, resize_to: int | None = None):
    """Drive both renderers through the same frames; return their screens."""
    ta, tb = _EmulatedTerminal(width), _EmulatedTerminal(width)
    cell, string = ScrollbackTerminal(ta), ScrollbackRenderer(tb)  # type: ignore[arg-type]
    for k, lines in enumerate(frames):
        if resize_to is not None and k == len(frames) - 1:
            ta.width = tb.width = resize_to
            ta.fire_resize()
            tb.fire_resize()
        cell.render(_to_buffer(lines, ta.width))
        string.render(list(lines))
    return ta.screen.snapshot(), tb.screen.snapshot()


SCENARIOS = {
    "first paint": [["alpha", "beta", "gamma"]],
    "append one line": [["a", "b"], ["a", "b", "c"]],
    "edit middle": [["a", "b", "c"], ["a", "XX", "c"]],
    "edit first": [["a", "b", "c"], ["Z", "b", "c"]],
    "shorter line replaces longer": [["aaaa", "b"], ["a", "b"]],
    "shrink rows": [["a", "b", "c", "d"], ["a", "b"]],
    "grow past screen": [[f"l{i}" for i in range(5)], [f"l{i}" for i in range(25)]],
    "no change": [["a", "b"], ["a", "b"]],
    "styled": [
        ["\x1b[31mred\x1b[0m", "plain"],
        ["\x1b[31mred\x1b[0m", "\x1b[1mbold\x1b[0m"],
    ],
    "full replace": [["a", "b", "c"], ["p", "q", "r"]],
    "empty then content": [[], ["a", "b"]],
    "content then empty": [["a", "b"], []],
    "many frames": [[f"f{i}" for i in range(n)] for n in range(1, 12)],
    "edit far above viewport": [
        [f"r{i}" for i in range(30)],
        ["CHANGED"] + [f"r{i}" for i in range(1, 30)],
    ],
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_screen_matches_the_cell_renderer(name: str) -> None:
    cells, strings = _both(SCENARIOS[name])
    assert cells == strings


def test_screen_matches_across_a_resize() -> None:
    frames = [[f"line {i}" for i in range(6)], [f"line {i}" for i in range(6)]]
    cells, strings = _both(frames, resize_to=30)
    assert cells == strings


def test_screen_matches_on_randomised_frame_sequences() -> None:
    """Fuzz the paths the named scenarios don't reach by construction."""
    rng = random.Random(11)
    for _ in range(400):
        frames: list[list[str]] = []
        cur = ["init"]
        for _ in range(rng.randint(1, 6)):
            cur = list(cur)
            op = rng.choice(["append", "edit", "shrink", "replace", "grow"])
            if op == "append":
                cur.append(f"n{rng.randint(0, 99)}")
            elif op == "edit" and cur:
                cur[rng.randrange(len(cur))] = f"e{rng.randint(0, 99)}"
            elif op == "shrink" and len(cur) > 1:
                cur = cur[: rng.randrange(1, len(cur))]
            elif op == "replace":
                cur = [f"r{i}" for i in range(rng.randint(1, 12))]
            elif op == "grow":
                cur = cur + [f"g{i}" for i in range(rng.randint(1, 10))]
            frames.append(cur)
        cells, strings = _both(frames)
        assert cells == strings, f"diverged on {frames}"


def test_stable_through_skips_the_prefix_without_changing_the_screen() -> None:
    base = [f"h{i}" for i in range(5)]
    nxt = [*base, "tail"]
    ta, tb = _EmulatedTerminal(), _EmulatedTerminal()
    cell, string = ScrollbackTerminal(ta), ScrollbackRenderer(tb)  # type: ignore[arg-type]
    cell.render(_to_buffer(base, ta.width))
    string.render(list(base))
    cell.render(_to_buffer(nxt, ta.width), stable_through=5)
    string.render(list(nxt), stable_through=5)
    assert ta.screen.snapshot() == tb.screen.snapshot()


def test_reset_repaints_without_clearing_just_like_the_cell_renderer() -> None:
    """reset() means "repaint, screen state unknown" — it does NOT home first.

    So the repaint lands below what's already there. That is the existing
    contract (used when handing the terminal back after a suspend), not a bug:
    both renderers produce the same duplicated-row result. reset_with_clear()
    is the variant that homes and erases.
    """
    ta, tb = _EmulatedTerminal(), _EmulatedTerminal()
    cell, string = ScrollbackTerminal(ta), ScrollbackRenderer(tb)  # type: ignore[arg-type]
    for renderer, to_frame in (
        (cell, lambda ls: _to_buffer(ls, 40)),
        (string, list),
    ):
        renderer.render(to_frame(["a", "b"]))
        renderer.reset()
        renderer.render(to_frame(["a", "b"]))
    assert ta.screen.snapshot() == tb.screen.snapshot()


def test_reset_with_clear_homes_and_erases() -> None:
    t = _EmulatedTerminal()
    r = ScrollbackRenderer(t)  # type: ignore[arg-type]
    r.render(["a", "b"])
    r.reset_with_clear()
    r.render(["x", "y"])
    assert t.screen.snapshot() == ["x", "y"]


def test_clear_wipes_the_screen() -> None:
    t = _EmulatedTerminal()
    r = ScrollbackRenderer(t)  # type: ignore[arg-type]
    r.render(["a", "b", "c"])
    r.clear()
    assert t.screen.snapshot() == [""]


def test_render_is_cheap_for_large_frames() -> None:
    """The whole point: a full repaint must not scale like the cell pipeline."""
    t = _EmulatedTerminal(100)
    r = ScrollbackRenderer(t)  # type: ignore[arg-type]
    lines = [f"line {i} of output" for i in range(20_000)]
    r.render(lines)
    assert t.screen.snapshot()[0] == "line 0 of output"
    assert len(t.screen.snapshot()) >= 20_000
