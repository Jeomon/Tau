"""Terminal output is handed to a writer thread so a slow terminal can't stall the loop.

A full-transcript repaint (every resize) is ~160 KiB. Against a terminal that
drains slowly — over SSH, or a local one busy reflowing that same resize — the
blocking write costs 100-400 ms *on the event loop*: no keystrokes, no
streaming, frozen spinner. ``_OutputWriter`` moves the waiting to its own
thread; these tests pin the properties that makes safe: strict ordering, a
``drain`` that really waits, and failures that still reach the caller.
"""

from __future__ import annotations

import threading
import time

import pytest

from tau.tui.terminal import Terminal, _OutputWriter


class _Sink:
    """Records writes, optionally stalling each one to mimic a slow terminal."""

    def __init__(self, delay: float = 0.0) -> None:
        self.chunks: list[str] = []
        self.flushes = 0
        self.delay = delay
        self._lock = threading.Lock()

    def write(self, data: str) -> None:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.chunks.append(data)

    def flush(self) -> None:
        with self._lock:
            self.flushes += 1

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self.chunks)


def test_writes_are_delivered_in_order() -> None:
    sink = _Sink()
    writer = _OutputWriter(sink)
    expected = [f"<{i}>" for i in range(2000)]
    for chunk in expected:
        writer.write(chunk)
    writer.close(timeout=5)

    assert sink.text == "".join(expected)


def test_drain_waits_for_a_slow_terminal() -> None:
    sink = _Sink(delay=0.05)
    writer = _OutputWriter(sink)
    writer.write("frame")
    writer.drain(timeout=5)

    # drain must not return before the byte actually reached the sink
    assert sink.text == "frame"
    writer.close(timeout=5)


def test_write_does_not_block_the_caller() -> None:
    sink = _Sink(delay=0.3)
    writer = _OutputWriter(sink)

    start = time.perf_counter()
    writer.write("x" * 100_000)
    handoff = time.perf_counter() - start

    # The whole point: handing off is immediate even though the sink is slow.
    assert handoff < 0.05, f"write() blocked for {handoff:.3f}s"
    writer.drain(timeout=5)
    writer.close(timeout=5)


def test_batched_writes_are_coalesced_into_one_syscall() -> None:
    """A resize burst should collapse into a single write, not one per frame."""
    sink = _Sink(delay=0.1)
    writer = _OutputWriter(sink)
    writer.write("first")  # claims the thread, stalls in the sink
    time.sleep(0.02)
    for i in range(50):  # queue up behind it
        writer.write(f"{i};")
    writer.close(timeout=5)

    assert sink.text == "first" + "".join(f"{i};" for i in range(50))
    assert len(sink.chunks) < 50  # coalesced, not one write per call


def test_writer_failure_surfaces_to_the_caller() -> None:
    """A dead terminal must raise, not silently stop painting on a daemon thread."""

    class Broken(_Sink):
        def write(self, data: str) -> None:
            raise BrokenPipeError("terminal went away")

    writer = _OutputWriter(Broken())
    writer.write("hello")

    with pytest.raises(BrokenPipeError):
        for _ in range(50):
            writer.drain(timeout=1)
            time.sleep(0.01)


def test_error_is_reported_once_then_cleared() -> None:
    class FlakyOnce(_Sink):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def write(self, data: str) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("transient")
            super().write(data)

    sink = FlakyOnce()
    writer = _OutputWriter(sink)
    writer.write("a")
    time.sleep(0.1)
    with pytest.raises(OSError, match="transient"):
        writer.drain(timeout=1)

    # Subsequent writes still work; the error isn't sticky.
    writer.write("b")
    writer.drain(timeout=5)
    assert sink.text == "b"
    writer.close(timeout=5)


def test_close_is_safe_without_any_write() -> None:
    writer = _OutputWriter(_Sink())
    writer.drain(timeout=1)
    writer.close(timeout=1)


def test_no_thread_is_started_until_something_is_written() -> None:
    before = threading.active_count()
    writer = _OutputWriter(_Sink())
    assert threading.active_count() == before
    writer.write("x")
    writer.drain(timeout=5)
    writer.close(timeout=5)


def test_non_tty_stdout_defaults_to_synchronous_output(monkeypatch) -> None:
    """Piped output (--print, tests, CI) keeps the simple synchronous path."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    assert Terminal()._writer is None


def test_async_output_can_be_forced_on() -> None:
    term = Terminal(async_output=True)
    assert term._writer is not None


def test_exit_raw_mode_drains_before_restoring_the_terminal() -> None:
    """Queued bytes must land before termios is restored or a child takes over."""
    term = Terminal(async_output=True)
    sink = _Sink(delay=0.05)
    assert term._writer is not None
    term._writer._stream = sink

    term.write("tail-of-final-frame")
    term.exit_raw_mode()  # no raw mode was entered; the drain still must happen

    assert sink.text == "tail-of-final-frame"
    term._writer.close(timeout=5)


def test_flush_is_a_handoff_and_drain_is_the_guarantee() -> None:
    term = Terminal(async_output=True)
    sink = _Sink(delay=0.05)
    assert term._writer is not None
    term._writer._stream = sink

    term.write("payload")
    term.flush()  # cheap handoff, makes no landing promise
    term.drain(timeout=5)  # this is the promise
    assert sink.text == "payload"
    term._writer.close(timeout=5)
