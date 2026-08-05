"""ScrollbackRenderer must put the right thing on screen.

These were differential tests against ScrollbackTerminal (the Cell-grid
renderer). That renderer is deleted, so the expected screens below were
captured from it while both pipelines still existed and were asserted to
agree — golden output, not a live oracle.

``Screen`` is a minimal terminal emulator covering exactly the sequences the
renderer emits (relative cursor moves, erases, scrolling). It is what makes
asserting on *resulting screen state* possible at all: the renderer's byte
stream is full of relative moves that say nothing on their own.

``Screen`` is also imported by test_string_renderer_integration.
"""

from __future__ import annotations

import random
import re

import pytest

from tau.tui.scrollback import ScrollbackRenderer
from tau.tui.utils import strip_ansi

_VIEWPORT_HEIGHT = 10  # matches _EmulatedTerminal's default height

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


def _screen(frames: list[list[str]], width: int = 40, resize_to: int | None = None):
    """Drive the renderer through the frames; return the resulting screen."""
    term = _EmulatedTerminal(width)
    renderer = ScrollbackRenderer(term)  # type: ignore[arg-type]
    for k, lines in enumerate(frames):
        if resize_to is not None and k == len(frames) - 1:
            term.width = resize_to
            term.fire_resize()
        renderer.render(list(lines))
    return [strip_ansi(r).rstrip() for r in term.screen.snapshot()]


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


GOLDENS = {
    "first paint": ["alpha", "beta", "gamma"],
    "append one line": ["a", "b", "c"],
    "edit middle": ["a", "XX", "c"],
    "edit first": ["Z", "b", "c"],
    "shorter line replaces longer": ["a", "b"],
    "shrink rows": ["a", "b", "", ""],
    "grow past screen": [
        "l0",
        "l1",
        "l2",
        "l3",
        "l4",
        "l5",
        "l6",
        "l7",
        "l8",
        "l9",
        "l10",
        "l11",
        "l12",
        "l13",
        "l14",
        "l15",
        "l16",
        "l17",
        "l18",
        "l19",
        "l20",
        "l21",
        "l22",
        "l23",
        "l24",
    ],
    "no change": ["a", "b"],
    "styled": ["red", "bold"],
    "full replace": ["p", "q", "r"],
    "empty then content": ["a", "b"],
    "content then empty": ["", ""],
    "many frames": ["f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
    "edit far above viewport": [
        "r0",
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
        "r6",
        "r7",
        "r8",
        "r9",
        "r10",
        "r11",
        "r12",
        "r13",
        "r14",
        "r15",
        "r16",
        "r17",
        "r18",
        "r19",
        "r20",
        "r21",
        "r22",
        "r23",
        "r24",
        "r25",
        "r26",
        "r27",
        "r28",
        "r29",
    ],
}
GOLDEN_RESIZE = ["line 0", "line 1", "line 2", "line 3", "line 4", "line 5"]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_screen_matches_the_expected_output(name: str) -> None:
    assert _screen(SCENARIOS[name]) == GOLDENS[name]


def test_screen_is_correct_across_a_resize() -> None:
    frames = [[f"line {i}" for i in range(6)], [f"line {i}" for i in range(6)]]
    assert _screen(frames, resize_to=30) == GOLDEN_RESIZE


def test_final_screen_always_shows_the_final_frame() -> None:
    """Oracle-free invariant: the screen ends up showing the last frame.

    Restricted to append/grow/edit sequences, which is what a transcript
    actually does. A frame that shrinks far enough to push earlier rows out of
    the viewport is deliberately *not* repainted above it — those rows have
    scrolled into the terminal's native scrollback, where CSI cannot address
    them, so the renderer only re-numbers rather than reprinting (see
    ScrollbackRenderer._render's pure-shift path). The cell renderer behaved
    identically; "shrink rows" in SCENARIOS pins that case explicitly.
    """
    rng = random.Random(11)
    for _ in range(400):
        frames: list[list[str]] = []
        cur = ["init"]
        for _ in range(rng.randint(1, 6)):
            cur = list(cur)
            op = rng.choice(["append", "edit", "grow"])
            if op == "append":
                cur.append(f"n{rng.randint(0, 99)}")
            elif op == "edit" and cur:
                # Only the live tail: rows that have scrolled past the viewport
                # are in native scrollback and intentionally not repainted.
                lo = max(0, len(cur) - _VIEWPORT_HEIGHT)
                cur[rng.randrange(lo, len(cur))] = f"e{rng.randint(0, 99)}"
            elif op == "grow":
                cur = cur + [f"g{i}" for i in range(rng.randint(1, 10))]
            frames.append(cur)
        got = _screen(frames)
        expected = [x.rstrip() for x in frames[-1]]
        assert got[: len(expected)] == expected, f"frames={frames}"
        assert all(x == "" for x in got[len(expected) :]), f"stale rows left: {got}"


def test_stable_through_skips_the_prefix_without_changing_the_screen() -> None:
    base = [f"h{i}" for i in range(5)]
    nxt = [*base, "tail"]
    term = _EmulatedTerminal()
    r = ScrollbackRenderer(term)  # type: ignore[arg-type]
    r.render(list(base))
    r.render(list(nxt), stable_through=5)
    assert [strip_ansi(x).rstrip() for x in term.screen.snapshot()] == nxt


def test_reset_repaints_without_clearing() -> None:
    """reset() means "repaint, screen state unknown" — it does NOT home first.

    So the repaint lands below what is already there, duplicating it. That is
    the contract (used when handing the terminal back after a suspend), not a
    bug; reset_with_clear() is the variant that homes and erases.
    """
    term = _EmulatedTerminal()
    r = ScrollbackRenderer(term)  # type: ignore[arg-type]
    r.render(["a", "b"])
    r.reset()
    r.render(["a", "b"])
    assert [strip_ansi(x).rstrip() for x in term.screen.snapshot()] == ["a", "a", "b"]


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
