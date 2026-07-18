"""Platform-independent tests for Windows reader teardown guards."""

import threading

from tau.tui.service import TUI


def test_late_windows_reader_callback_is_discarded_after_shutdown() -> None:
    tui = object.__new__(TUI)
    tui._stdin_shutdown = threading.Event()
    tui._stdin_generation = 3
    tui._running = True
    received: list[str] = []
    tui._process_input = received.append

    tui._process_windows_input("before", 3)
    tui._stdin_shutdown.set()
    tui._process_windows_input("after-shutdown", 3)
    tui._stdin_shutdown.clear()
    tui._process_windows_input("after-restart", 2)

    assert received == ["before"]
