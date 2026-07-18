"""Regression tests for input-path robustness in the TUI.

1. Incomplete escape sequences must never wedge the parser: Alt+] (ESC ]) or
   Alt+Shift+P (ESC P) leave an unterminated OSC/DCS introducer that only
   completes on BEL/ST, and Alt+[ leaves a dangling CSI — the ESC-flush timer
   must fire for *any* incomplete escape-prefixed buffer, not just a bare ESC,
   or every later keystroke is appended to the pending sequence and swallowed
   forever. An in-progress bracketed paste must NOT be flushed.

2. A throwing input handler (e.g. from an extension) must not abort the rest
   of the dispatch chain or the remaining event batch.
"""

from __future__ import annotations

import asyncio

from tau.tui.input import InputParser, KeyEvent
from tau.tui.service import TUI


class FakeTerminal:
    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        return lambda: None


class TestParserFlushRecoversFromDanglingIntroducers:
    def test_dangling_osc_swallows_keys_until_flush(self):
        parser = InputParser()
        assert parser.feed("\x1b]") == []
        assert parser.feed("abc") == []  # swallowed into the pending OSC
        parser.flush()
        assert parser._buf == ""
        events = parser.feed("x")
        assert len(events) == 1
        assert isinstance(events[0], KeyEvent)
        assert events[0].key == "x"

    def test_dangling_dcs_swallows_keys_until_flush(self):
        parser = InputParser()
        assert parser.feed("\x1bP") == []  # Alt+Shift+P: DCS introducer
        assert parser.feed("qq") == []
        parser.flush()
        assert parser._buf == ""
        assert [e.key for e in parser.feed("y") if isinstance(e, KeyEvent)] == ["y"]


class TestEscFlushScheduling:
    def _tui(self) -> TUI:
        return TUI(terminal=FakeTerminal())  # type: ignore[arg-type]

    def test_bare_esc_still_schedules_flush(self):
        async def scenario():
            tui = self._tui()
            tui._process_input("\x1b")
            assert tui._esc_timer is not None
            tui._cancel_timers()

        asyncio.run(scenario())

    def test_dangling_osc_introducer_schedules_flush(self):
        async def scenario():
            tui = self._tui()
            tui._process_input("\x1b]")  # Alt+]
            assert tui._esc_timer is not None
            # Later keystrokes are swallowed until the flush fires...
            tui._process_input("hello")
            assert tui._parser._buf == "\x1b]hello"
            # ...then the flush drops the dangling sequence and the parser
            # accepts input again.
            tui._flush_esc()
            assert tui._parser._buf == ""
            seen: list[str] = []
            tui.on_input(lambda e: seen.append(e.key) or None)
            tui._process_input("x")
            assert seen == ["x"]
            tui._cancel_timers()

        asyncio.run(scenario())

    def test_dangling_csi_introducer_schedules_flush(self):
        async def scenario():
            tui = self._tui()
            tui._process_input("\x1b[")  # Alt+[
            assert tui._esc_timer is not None
            tui._flush_esc()
            assert tui._parser._buf == ""
            tui._cancel_timers()

        asyncio.run(scenario())

    def test_in_progress_bracketed_paste_is_not_flushed(self):
        async def scenario():
            tui = self._tui()
            # An armed timer from an ambiguous prefix must be cancelled once
            # the buffer turns out to be a bracketed paste in progress —
            # flushing mid-paste would emit the partial paste as garbage.
            tui._process_input("\x1b[2")
            assert tui._esc_timer is not None
            tui._process_input("00~pasted content")
            assert tui._esc_timer is None
            assert tui._parser._buf.startswith("\x1b[200~")
            # Terminator arrives: paste is delivered normally.
            events: list = []
            tui.on_input(lambda e: events.append(e) or None)
            tui._process_input("\x1b[201~")
            assert tui._parser._buf == ""
            assert len(events) == 1
            assert events[0].text == "pasted content"
            tui._cancel_timers()

        asyncio.run(scenario())


class TestDispatchHandlerExceptions:
    def _tui(self) -> TUI:
        return TUI(terminal=FakeTerminal())  # type: ignore[arg-type]

    def test_throwing_intercept_handler_does_not_block_chain(self):
        tui = self._tui()
        seen: list[str] = []

        def bad(event):
            raise RuntimeError("boom")

        tui.on_input_intercept(bad)
        tui.on_input(lambda e: seen.append(e.key) or None)

        tui._dispatch(KeyEvent(key="a"))
        assert seen == ["a"]

    def test_throwing_global_handler_does_not_stop_later_handlers(self):
        tui = self._tui()
        seen: list[str] = []

        def bad(event):
            raise RuntimeError("boom")

        tui.on_input(bad)
        tui.on_input(lambda e: seen.append(e.key) or None)

        tui._dispatch(KeyEvent(key="b"))
        assert seen == ["b"]
