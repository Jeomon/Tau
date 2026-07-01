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
