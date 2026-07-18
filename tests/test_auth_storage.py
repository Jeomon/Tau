"""Tests for tau/auth/storage.py — InMemoryAuthStorage."""

from __future__ import annotations

from tau.auth.storage import InMemoryAuthStorage
from tau.auth.types import LockResult


class TestInMemoryAuthStorage:
    def test_initial_value_is_none(self):
        s = InMemoryAuthStorage()
        result = s.with_lock(lambda v: LockResult(result=v))
        assert result.result is None

    def test_with_lock_passes_current_value(self):
        s = InMemoryAuthStorage()
        s.with_lock(lambda _: LockResult(result=None, next='{"key": "val"}'))
        result = s.with_lock(lambda v: LockResult(result=v))
        assert result.result == '{"key": "val"}'

    def test_next_updates_stored_value(self):
        s = InMemoryAuthStorage()
        s.with_lock(lambda _: LockResult(result=None, next="new-value"))
        result = s.with_lock(lambda v: LockResult(result=v))
        assert result.result == "new-value"

    def test_next_none_does_not_overwrite(self):
        s = InMemoryAuthStorage()
        s.with_lock(lambda _: LockResult(result=None, next="original"))
        s.with_lock(lambda v: LockResult(result=v, next=None))
        result = s.with_lock(lambda v: LockResult(result=v))
        assert result.result == "original"

    def test_sequential_writes(self):
        s = InMemoryAuthStorage()
        s.with_lock(lambda _: LockResult(result=None, next="first"))
        s.with_lock(lambda _: LockResult(result=None, next="second"))
        result = s.with_lock(lambda v: LockResult(result=v))
        assert result.result == "second"

    def test_result_returned_from_fn(self):
        s = InMemoryAuthStorage()
        result = s.with_lock(lambda _: LockResult(result=42))
        assert result.result == 42


class TestFileAuthStorage:
    def test_creates_file_on_init(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        FileAuthStorage(store_path)
        assert store_path.exists()

    def test_initial_content_is_empty_json(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        FileAuthStorage(store_path)
        assert store_path.read_text() == "{}"

    def test_with_lock_reads_current_value(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        storage = FileAuthStorage(store_path)
        result = storage.with_lock(lambda v: LockResult(result=v))
        assert result.result == "{}"

    def test_with_lock_writes_next_value(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        storage = FileAuthStorage(store_path)
        storage.with_lock(lambda _: LockResult(result=None, next='{"token": "abc"}'))
        result = storage.with_lock(lambda v: LockResult(result=v))
        assert result.result == '{"token": "abc"}'

    def test_no_write_when_next_is_none(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        storage = FileAuthStorage(store_path)
        storage.with_lock(lambda _: LockResult(result=None, next='{"initial": 1}'))
        storage.with_lock(lambda _: LockResult(result=None, next=None))
        result = storage.with_lock(lambda v: LockResult(result=v))
        import json

        assert json.loads(result.result)["initial"] == 1

    def test_creates_parent_dir(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        nested = tmp_path / "a" / "b" / "auth.json"
        FileAuthStorage(nested)
        assert nested.exists()

    def test_with_lock_async(self, tmp_path):
        import asyncio

        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        storage = FileAuthStorage(store_path)

        async def _run():
            return await storage.with_lock_async(lambda _: _async_result('{"async": true}'))

        async def _async_result(val: str) -> LockResult:
            return LockResult(result=None, next=val)

        asyncio.run(_run())
        import json

        assert json.loads(store_path.read_text())["async"] is True

    def test_with_lock_async_does_not_deadlock_on_concurrent_callers(self, tmp_path):
        """A second with_lock_async() call must not block the event loop while
        waiting for the lock — the first call's fn is awaited *while the lock
        is held* (an OAuth refresh does a real network call there), so a
        synchronous blocking wait for the same lock would deadlock: neither
        coroutine could ever make progress, since the first needs the event
        loop free to resolve its await, and the second's blocking wait is
        what's occupying it. Simulates that shape: caller A holds the lock
        across an await gated on an event only caller B (running concurrently
        on the same loop) can set.
        """
        import asyncio

        from tau.auth.storage import FileAuthStorage

        store_path = tmp_path / "auth.json"
        storage = FileAuthStorage(store_path)

        async def _run():
            b_started = asyncio.Event()
            a_may_finish = asyncio.Event()
            order: list[str] = []

            async def a_fn(_current: str | None) -> LockResult:
                order.append("a_holds_lock")
                # Must yield here for b_task to actually start and attempt its
                # own lock acquisition *while a still holds the lock* — the
                # exact overlap that deadlocks with a blocking FileLock.
                b_started.set()
                await a_may_finish.wait()
                order.append("a_releasing")
                return LockResult(result=None, next='{"a": true}')

            async def b_fn(_current: str | None) -> LockResult:
                order.append("b_holds_lock")
                return LockResult(result=None, next='{"b": true}')

            async def caller_a():
                await storage.with_lock_async(a_fn)

            async def caller_b():
                await b_started.wait()
                await storage.with_lock_async(b_fn)

            async def releaser():
                # Give b_task a moment to actually reach its lock-acquisition
                # attempt before letting a finish — proves b was genuinely
                # waiting on the lock, not just winning a scheduling race.
                await asyncio.sleep(0.05)
                a_may_finish.set()

            a_task = asyncio.ensure_future(caller_a())
            b_task = asyncio.ensure_future(caller_b())
            r_task = asyncio.ensure_future(releaser())

            await asyncio.wait_for(asyncio.gather(a_task, b_task, r_task), timeout=5.0)
            return order

        order = asyncio.run(_run())

        # b can only acquire the lock after a releases it — the real
        # assertion is just that this returns at all within the timeout
        # instead of deadlocking.
        assert order == ["a_holds_lock", "a_releasing", "b_holds_lock"]
