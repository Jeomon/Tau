"""Shared poll timing for RFC 8628 device-code logins.

Three providers run the device flow (GitHub Copilot, OpenAI Codex, xAI Grok)
against endpoints that agree on the *shape* of the exchange and nothing else:
each has its own request format, its own way of spelling "still pending", and
its own terminal errors. What they do share is the clock — deadline, poll
interval, the minimum interval, and how a ``slow_down`` response widens it —
and that arithmetic was copied into all three, subtly diverging as it went.

``DeviceCodePoller`` owns only the clock. Classification stays with each
provider, where the protocol differences actually live::

    poller = DeviceCodePoller(interval_seconds=5, expires_in=900, signal=signal)
    async for tick in poller:
        status, data = await asyncio.to_thread(poll_once)
        if approved(status, data):
            return extract(data)
        if data.get("error") == "slow_down":
            tick.slow_down(data.get("interval"))
        elif not pending(status, data):
            raise RuntimeError(...)
    raise RuntimeError("timed out")

The loop waits *before* the first attempt, matching the RFC: the user has to
be given time to type the code somewhere else.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from tau.inference.provider.oauth.types import AbortSignal

__all__ = ["DeviceCodePoller"]

# RFC 8628 §3.5 sets 5s as the default interval and requires clients to honour a
# server-supplied one; a second is the floor for a server that sends 0 or omits
# it, so a broken value cannot turn the loop into a busy-wait.
_MIN_INTERVAL_MS = 1000
# The RFC's prescribed increase when the server answers `slow_down`.
_SLOW_DOWN_INCREMENT_MS = 5000


@dataclass
class DeviceCodePoller:
    """Async iterator that yields once per device-code poll attempt.

    Iteration ends when ``expires_in`` runs out, leaving the caller to raise
    whatever timeout error fits its provider. An aborted signal raises
    ``abort_message`` from inside the loop instead.
    """

    interval_seconds: float
    expires_in: float
    signal: AbortSignal | None = None
    abort_message: str = "Device code login aborted"
    # GitHub pads the server's interval: 1.2x while waiting, 1.4x once it has
    # asked us to slow down. The others poll at exactly the stated interval.
    interval_multiplier: float = 1.0
    slow_down_multiplier: float = 1.0

    #: How many `slow_down` responses arrived. A timeout that follows one is
    #: usually clock drift (WSL, VMs) rather than an unresponsive user, which
    #: is worth telling the operator.
    slow_downs: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._interval_ms = max(_MIN_INTERVAL_MS, self.interval_seconds * 1000)
        self._deadline = time.time() + self.expires_in
        self._multiplier = self.interval_multiplier

    async def __aiter__(self) -> AsyncIterator[DeviceCodePoller]:
        while time.time() < self._deadline:
            if self.signal is not None and self.signal.is_set():
                raise RuntimeError(self.abort_message)
            # Never sleep past the deadline: the last wait should end the loop
            # promptly rather than overshooting by most of an interval.
            remaining = self._deadline - time.time()
            await asyncio.sleep(min(self._interval_ms * self._multiplier / 1000, remaining))
            yield self

    def slow_down(self, server_interval: object = None) -> None:
        """Widen the interval after a ``slow_down`` response.

        ``server_interval`` is whatever the endpoint sent (seconds) and is
        taken at face value when it is a usable number; otherwise the interval
        grows by the RFC's 5 seconds. Pass nothing for endpoints that do not
        restate an interval.
        """
        self.slow_downs += 1
        self._multiplier = self.slow_down_multiplier
        usable = (
            isinstance(server_interval, (int, float))
            and not isinstance(server_interval, bool)
            and server_interval > 0
        )
        if usable:
            self._interval_ms = max(_MIN_INTERVAL_MS, float(server_interval) * 1000)  # type: ignore[arg-type]
        else:
            self._interval_ms = max(_MIN_INTERVAL_MS, self._interval_ms + _SLOW_DOWN_INCREMENT_MS)
