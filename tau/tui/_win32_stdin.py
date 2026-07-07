"""Event-driven Windows stdin watcher.

Windows event loops cannot ``add_reader()`` a console handle the way POSIX
loops can watch a file descriptor, so Tau previously ran a dedicated daemon
thread that spent its entire life inside a blocking ``os.read`` loop,
marshaling each chunk back to the event loop via ``call_soon_threadsafe``.

This replaces that permanent thread with ``WaitForMultipleObjects`` on the
console's stdin handle, submitted to asyncio's default executor one wait at
a time — each wait only borrows a thread-pool thread for as long as it takes
for the next keystroke to arrive, and shutdown is a signaled event instead of
waiting for the next blocking read to notice a stop flag.

``_wait_for_handles``/``_create_win32_event`` are a trimmed vendor of
``prompt_toolkit.eventloop.win32``'s ``wait_for_handles``/
``create_win32_event`` (BSD-3-Clause,
https://github.com/prompt-toolkit/python-prompt-toolkit) — the smallest
self-contained piece of that project's Windows input handling; everything
else there (its own ``KeyPress``/``Keys`` model, mouse events, VT parser)
duplicates what Tau's own ``InputParser`` already does and was left alone.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
from collections.abc import Callable
from ctypes import Structure, pointer
from ctypes.wintypes import BOOL, DWORD, HANDLE, LPVOID
from typing import Any

_STD_INPUT_HANDLE = -10
_WAIT_TIMEOUT = 0x00000102
_INFINITE = -1


def _kernel32() -> Any:
    """Return ``ctypes.windll.kernel32``, narrowed so type checkers see it on any platform.

    typeshed only declares ``ctypes.windll`` under a ``sys.platform ==
    "win32"`` guard, so the attribute access needs that check in its own
    scope — centralized here once instead of repeated at each call site.
    This module is only ever imported from within an
    ``if sys.platform == "win32":`` branch (see ``service.py``), so the branch
    below always takes the ``win32`` path at runtime.
    """
    if sys.platform != "win32":  # pragma: no cover - keeps type checkers on win32 stubs
        raise RuntimeError("Win32StdinWatcher is Windows-only")
    return ctypes.windll.kernel32


class _SecurityAttributes(Structure):
    _fields_ = [
        ("nLength", DWORD),
        ("lpSecurityDescriptor", LPVOID),
        ("bInheritHandle", BOOL),
    ]


def _wait_for_handles(handles: list[HANDLE], timeout: int = _INFINITE) -> HANDLE | None:
    """Block until one of ``handles`` is signaled; return it, or None on timeout.

    ``handles`` must be a list of ``HANDLE`` objects (not raw ints) — ctypes
    otherwise treats them as 4-byte values, which silently truncates the
    8-byte handles Windows actually hands out.
    """
    arrtype = HANDLE * len(handles)
    handle_array = arrtype(*handles)
    ret: int = _kernel32().WaitForMultipleObjects(
        len(handle_array), handle_array, BOOL(False), DWORD(timeout)
    )
    if ret == _WAIT_TIMEOUT:
        return None
    return handles[ret]


def _create_win32_event() -> HANDLE:
    """Create an unnamed, manual-reset Win32 event, initially unsignaled."""
    return HANDLE(
        _kernel32().CreateEventA(
            pointer(_SecurityAttributes()),
            BOOL(True),
            BOOL(False),
            None,
        )
    )


class Win32StdinWatcher:
    """Watches the console's stdin handle without a permanently-blocked thread.

    ``on_ready`` is invoked on the event-loop thread (via
    ``call_soon_threadsafe``) whenever stdin has data available, matching how
    POSIX's ``add_reader`` callback runs. ``stop()`` signals a remove-event so
    an in-flight wait unblocks immediately instead of lingering until the next
    keystroke.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, on_ready: Callable[[], None]) -> None:
        self._loop = loop
        self._on_ready = on_ready
        self._handle = HANDLE(_kernel32().GetStdHandle(_STD_INPUT_HANDLE))
        self._remove_event = _create_win32_event()
        self._stopped = False

    def start(self) -> None:
        self._loop.run_in_executor(None, self._wait)

    def stop(self) -> None:
        self._stopped = True
        _kernel32().SetEvent(self._remove_event)

    def _wait(self) -> None:
        result = _wait_for_handles([self._remove_event, self._handle])
        if self._stopped or result is self._remove_event:
            _kernel32().CloseHandle(self._remove_event)
            return
        self._loop.call_soon_threadsafe(self._ready)

    def _ready(self) -> None:
        if self._stopped:
            return
        try:
            self._on_ready()
        finally:
            if not self._stopped:
                self._loop.run_in_executor(None, self._wait)
