"""Tests for tau/settings/storage.py — InMemorySettingsStorage."""

from __future__ import annotations

import json

from tau.settings.storage import InMemorySettingsStorage
from tau.settings.types import SCOPE, LockResult


def _storage() -> InMemorySettingsStorage:
    return InMemorySettingsStorage()


class TestInMemorySettingsStorageInitialState:
    def test_global_data_starts_empty_json(self):
        s = _storage()
        assert json.loads(s.global_data) == {}

    def test_project_data_starts_empty_json(self):
        s = _storage()
        assert json.loads(s.project_data) == {}


class TestInMemorySettingsStorageRead:
    def test_read_global_scope_gets_global_data(self):
        s = _storage()
        s.global_data = '{"key": "global_val"}'
        results = []
        s.with_lock(SCOPE.GLOBAL, lambda data: (results.append(data), LockResult(result=data))[1])
        assert results[0] == '{"key": "global_val"}'

    def test_read_project_scope_gets_project_data(self):
        s = _storage()
        s.project_data = '{"key": "project_val"}'
        results = []
        s.with_lock(SCOPE.PROJECT, lambda data: (results.append(data), LockResult(result=data))[1])
        assert results[0] == '{"key": "project_val"}'


class TestInMemorySettingsStorageWrite:
    def test_write_global_scope_updates_global_data(self):
        s = _storage()
        new_json = '{"theme": "dark"}'
        s.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next=new_json))
        assert s.global_data == new_json

    def test_write_project_scope_updates_project_data(self):
        s = _storage()
        new_json = '{"model": "claude"}'
        s.with_lock(SCOPE.PROJECT, lambda _: LockResult(result=None, next=new_json))
        assert s.project_data == new_json

    def test_write_global_does_not_affect_project(self):
        s = _storage()
        s.project_data = '{"original": true}'
        s.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next='{"changed": true}'))
        assert json.loads(s.project_data) == {"original": True}

    def test_write_project_does_not_affect_global(self):
        s = _storage()
        s.global_data = '{"preserved": true}'
        s.with_lock(SCOPE.PROJECT, lambda _: LockResult(result=None, next='{"changed": true}'))
        assert json.loads(s.global_data) == {"preserved": True}

    def test_next_none_does_not_overwrite(self):
        s = _storage()
        s.global_data = '{"existing": "data"}'
        s.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next=None))
        assert s.global_data == '{"existing": "data"}'


class TestInMemorySettingsStorageLockResult:
    def test_returns_result_from_fn(self):
        s = _storage()
        lr = s.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=42))
        assert lr.result == 42

    def test_result_can_be_parsed_json(self):
        s = _storage()
        s.global_data = '{"val": 99}'
        lr = s.with_lock(SCOPE.GLOBAL, lambda data: LockResult(result=json.loads(data)))
        assert lr.result == {"val": 99}

    def test_sequential_writes_accumulate(self):
        s = _storage()
        s.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next='{"count": 1}'))
        s.with_lock(
            SCOPE.GLOBAL,
            lambda data: LockResult(result=None, next=json.dumps({**json.loads(data), "count": 2})),
        )
        assert json.loads(s.global_data)["count"] == 2


class TestFileSettingsStorage:
    def test_creates_parent_directory(self, tmp_path):
        from tau.settings.storage import FileSettingsStorage

        nested = tmp_path / "a" / "b" / "c"
        storage = FileSettingsStorage(cwd=tmp_path, config_dir=nested)
        assert (nested / "settings.json").parent.exists()

    def test_global_lock_reads_empty_json_when_missing(self, tmp_path):
        from tau.settings.storage import FileSettingsStorage

        storage = FileSettingsStorage(cwd=tmp_path, config_dir=tmp_path)
        result = storage.with_lock(SCOPE.GLOBAL, lambda v: LockResult(result=v))
        raw: str = result.result  # type: ignore[assignment]
        assert json.loads(raw) == {}

    def test_global_lock_writes_value(self, tmp_path):
        from tau.settings.storage import FileSettingsStorage

        storage = FileSettingsStorage(cwd=tmp_path, config_dir=tmp_path)
        storage.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next='{"x": 1}'))
        result = storage.with_lock(SCOPE.GLOBAL, lambda v: LockResult(result=v))
        raw: str = result.result  # type: ignore[assignment]
        assert json.loads(raw)["x"] == 1

    def test_project_lock_uses_cwd_path(self, tmp_path):
        from tau.settings.storage import FileSettingsStorage

        cwd = tmp_path / "project"
        cwd.mkdir()
        storage = FileSettingsStorage(cwd=cwd, config_dir=tmp_path)
        storage.with_lock(SCOPE.PROJECT, lambda _: LockResult(result=None, next='{"proj": true}'))
        result = storage.with_lock(SCOPE.PROJECT, lambda v: LockResult(result=v))
        raw: str = result.result  # type: ignore[assignment]
        assert json.loads(raw)["proj"] is True

    def test_no_write_when_next_is_none(self, tmp_path):
        from tau.settings.storage import FileSettingsStorage

        storage = FileSettingsStorage(cwd=tmp_path, config_dir=tmp_path)
        storage.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next='{"initial": 1}'))
        storage.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next=None))
        result = storage.with_lock(SCOPE.GLOBAL, lambda v: LockResult(result=v))
        raw: str = result.result  # type: ignore[assignment]
        assert json.loads(raw)["initial"] == 1
