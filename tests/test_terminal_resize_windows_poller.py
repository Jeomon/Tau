"""Regression: the Windows resize poller must actually publish the new size.

Windows delivers no SIGWINCH, so ``Terminal`` polls the console size on a
daemon thread. ``_publish_size`` drops any value that is not the latest one
staged on ``_pending_size`` (that check is what keeps a stale queued size from
overwriting a fresh direct read). The poller used to post straight to
``_publish_size`` without staging, so every value it posted was dropped and no
resize was ever observed on Windows.
"""

from __future__ import annotations

import asyncio

from tau.tui.terminal import Terminal


class _StopAfter:
    """Stands in for the poller's stop Event: lets ``n`` ticks through."""

    def __init__(self, n: int) -> None:
        self._left = n

    def wait(self, _timeout: float) -> bool:
        if self._left <= 0:
            return True
        self._left -= 1
        return False


def _terminal(size: tuple[int, int]) -> Terminal:
    term = Terminal(async_output=False)
    term.width, term.height = size
    return term


def test_poller_publishes_without_a_loop() -> None:
    term = _terminal((100, 30))
    term._loop = None
    fired: list[tuple[int, int]] = []
    term.on_resize(lambda: fired.append((term.width, term.height)))

    term._get_size = staticmethod(lambda: (120, 40))  # type: ignore[method-assign]
    term._win_resize_poll_loop(_StopAfter(1))  # type: ignore[arg-type]

    assert (term.width, term.height) == (120, 40)
    assert fired == [(120, 40)]


def test_poller_publishes_through_the_event_loop() -> None:
    """The posted size must survive the _pending_size check on the loop thread."""
    term = _terminal((100, 30))
    fired: list[tuple[int, int]] = []
    term.on_resize(lambda: fired.append((term.width, term.height)))
    term._get_size = staticmethod(lambda: (120, 40))  # type: ignore[method-assign]

    async def main() -> None:
        term._loop = asyncio.get_running_loop()
        await asyncio.get_running_loop().run_in_executor(
            None, term._win_resize_poll_loop, _StopAfter(1)
        )
        await asyncio.sleep(0)  # let the queued call_soon_threadsafe run

    asyncio.run(main())

    assert (term.width, term.height) == (120, 40)
    assert fired == [(120, 40)]


def test_only_the_latest_polled_size_is_adopted() -> None:
    """A burst mid-drag stages each value; superseded ones are still dropped."""
    term = _terminal((100, 30))
    sizes = iter([(110, 30), (120, 40)])
    term._get_size = staticmethod(lambda: next(sizes))  # type: ignore[method-assign]
    fired: list[tuple[int, int]] = []
    term.on_resize(lambda: fired.append((term.width, term.height)))

    async def main() -> None:
        term._loop = asyncio.get_running_loop()
        await asyncio.get_running_loop().run_in_executor(
            None, term._win_resize_poll_loop, _StopAfter(2)
        )
        await asyncio.sleep(0)

    asyncio.run(main())

    assert (term.width, term.height) == (120, 40)
    assert fired == [(120, 40)]
