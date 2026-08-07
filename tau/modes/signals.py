"""Signal handling shared by the headless modes.

``rpc``, ``print`` and ``json`` all run without a terminal, driven by a script,
an editor plugin or a CI step — so they tend to be *killed* rather than quit.
Two things follow from that, and both are easy to get wrong in one mode and not
the other:

* The run must stop cleanly: abort the turn so in-flight tools stop and the
  session is written out, rather than leaving the agent streaming into a pipe
  nobody is reading.
* The process must exit with the conventional code, so whatever supervises it
  can tell "the client closed stdin" from "we killed it". Exiting 0 on SIGTERM
  makes a killed run look like a successful one.

``SIGINT`` is deliberately not handled here. In print mode Python's own
``KeyboardInterrupt`` already unwinds the run, and taking it over would swallow
Ctrl-C; RPC mode handles it itself as a graceful stop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterator

_log = logging.getLogger(__name__)

# 128 + signal number, the convention every shell reports.
EXIT_SIGHUP = 129
EXIT_SIGTERM = 143

_SIGNAL_EXIT_CODES = (("SIGTERM", EXIT_SIGTERM), ("SIGHUP", EXIT_SIGHUP))


class Interrupted(Exception):
    """A signal stopped the run; carries the exit code to report."""

    def __init__(self, code: int) -> None:
        super().__init__(f"interrupted (exit {code})")
        self.code = code


@contextlib.contextmanager
def exit_on_signal(on_signal: Callable[[], None]) -> Iterator[dict[str, int]]:
    """Record a conventional exit code for SIGTERM/SIGHUP, running ``on_signal``.

    Yields a dict that gains a ``"code"`` key if a signal arrives; the caller
    raises :class:`Interrupted` with it once the run has unwound, so the
    session is still written out before the process exits. The first signal
    wins — a second one during shutdown must not rewrite the reported cause.

    Handlers are removed on the way out. They are installed on the shared event
    loop, so leaving one behind would have a later run inherit a callback
    pointing at a dead runtime.
    """
    import signal as _signal

    loop = asyncio.get_event_loop()
    interrupted: dict[str, int] = {}
    installed: list[int] = []

    def _handle(code: int) -> None:
        interrupted.setdefault("code", code)
        try:
            on_signal()
        except Exception:  # a failing handler must not mask the signal
            _log.debug("signal handler raised", exc_info=True)

    for name, code in _SIGNAL_EXIT_CODES:
        sig = getattr(_signal, name, None)  # SIGHUP does not exist on Windows
        if sig is None:
            continue
        # Windows / Proactor loop → add_signal_handler raises.
        with contextlib.suppress(NotImplementedError, OSError):
            loop.add_signal_handler(sig, _handle, code)
            installed.append(sig)
    try:
        yield interrupted
    finally:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, OSError):
                loop.remove_signal_handler(sig)


def raise_if_interrupted(interrupted: dict[str, int]) -> None:
    """Re-raise a recorded signal as :class:`Interrupted`, if one arrived."""
    if code := interrupted.get("code"):
        raise Interrupted(code)
