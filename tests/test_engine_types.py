"""Tests for tau/engine/types.py — SteeringMode, FollowupMode, message queues."""

from __future__ import annotations

import asyncio

from tau.engine.types import FollowupMode, FollowupQueue, SteeringMode, SteeringQueue
from tau.message.types import UserMessage


def run(coro):
    return asyncio.run(coro)


class TestSteeringMode:
    def test_values(self):
        assert SteeringMode.OneAtATime == "one_at_a_time"
        assert SteeringMode.All == "all"

    def test_string_comparison(self):
        assert SteeringMode.OneAtATime == "one_at_a_time"


class TestFollowupMode:
    def test_values(self):
        assert FollowupMode.OneAtATime == "one_at_a_time"
        assert FollowupMode.All == "all"


class TestFollowupQueue:
    def _queue(self, mode=FollowupMode.OneAtATime):
        return FollowupQueue(mode=mode)

    def test_is_empty_initially(self):
        q = self._queue()
        assert q.is_empty() is True

    def test_enqueue_and_dequeue_one_at_a_time(self):
        q = self._queue(FollowupMode.OneAtATime)
        msg = UserMessage.from_text("hello")

        async def _test():
            await q.enqueue(msg)
            await q.enqueue(UserMessage.from_text("world"))
            msgs = await q.dequeue()
            return msgs

        result = run(_test())
        assert len(result) == 1
        assert result[0] is msg

    def test_enqueue_and_dequeue_all(self):
        q = self._queue(FollowupMode.All)

        async def _test():
            await q.enqueue(UserMessage.from_text("a"))
            await q.enqueue(UserMessage.from_text("b"))
            return await q.dequeue()

        result = run(_test())
        assert len(result) == 2

    def test_dequeue_empty_returns_empty_list(self):
        q = self._queue()
        result = run(q.dequeue())
        assert result == []

    def test_clear_removes_all_messages(self):
        q = self._queue()

        async def _test():
            await q.enqueue(UserMessage.from_text("hello"))
            q.clear()
            assert q.is_empty() is True

        run(_test())

    def test_snapshot_is_non_destructive(self):
        q = self._queue()

        async def _test():
            msg = UserMessage.from_text("peek")
            await q.enqueue(msg)
            snap = q.snapshot()
            assert len(snap) == 1
            assert not q.is_empty()

        run(_test())


class TestSteeringQueue:
    def test_one_at_a_time_mode(self):
        q = SteeringQueue(mode=SteeringMode.OneAtATime)

        async def _test():
            await q.enqueue(UserMessage.from_text("steer"))
            return await q.dequeue()

        result = run(_test())
        assert len(result) == 1

    def test_all_mode(self):
        q = SteeringQueue(mode=SteeringMode.All)

        async def _test():
            await q.enqueue(UserMessage.from_text("a"))
            await q.enqueue(UserMessage.from_text("b"))
            return await q.dequeue()

        result = run(_test())
        assert len(result) == 2
