"""Regression tests for TrustStore durability — locked read-modify-write,
atomic replacement, and corrupt-store preservation (never silently destroy
the user's trust decisions)."""

from __future__ import annotations

import json
import threading

from tau.trust.manager import TrustStore


def _store(tmp_path) -> TrustStore:
    return TrustStore(config_dir=tmp_path)


class TestCorruptStorePreservation:
    def test_get_on_corrupt_store_returns_none_and_backs_up(self, tmp_path):
        (tmp_path / "trust.json").write_text("{not json", encoding="utf-8")
        store = _store(tmp_path)
        assert store.get("/some/project") is None
        backups = list(tmp_path.glob("trust.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{not json"

    def test_set_after_corruption_keeps_original_recoverable(self, tmp_path):
        (tmp_path / "trust.json").write_text("{not json", encoding="utf-8")
        store = _store(tmp_path)
        store.set("/some/project", True)
        # The store recovered and persisted the new decision...
        data = json.loads((tmp_path / "trust.json").read_text(encoding="utf-8"))
        assert data.get("/some/project") is True
        # ...but the corrupt original was preserved, not silently overwritten.
        backups = list(tmp_path.glob("trust.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{not json"


class TestLockedMutations:
    def test_concurrent_sets_do_not_lose_entries(self, tmp_path):
        store = _store(tmp_path)

        def worker(i: int) -> None:
            for j in range(10):
                store.set(f"/proj-{i}-{j}", True)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = json.loads((tmp_path / "trust.json").read_text(encoding="utf-8"))
        assert len(data) == 40

    def test_set_roundtrip_still_works(self, tmp_path):
        store = _store(tmp_path)
        store.set("/my/project", True)
        store.set("/my/other", False)
        assert _store(tmp_path).get("/my/project") is True
        assert _store(tmp_path).get("/my/other") is False
