"""Tests for tau/trust/manager.py — TrustStore get/set/apply_option."""

from __future__ import annotations

from tau.trust.manager import TrustStore
from tau.trust.types import TrustOption


def _store(tmp_path) -> TrustStore:
    return TrustStore(config_dir=tmp_path)


class TestTrustStoreGet:
    def test_returns_none_for_unknown_path(self, tmp_path):
        store = _store(tmp_path)
        assert store.get("/unknown/project") is None

    def test_returns_decision_for_exact_path(self, tmp_path):
        store = _store(tmp_path)
        store.set("/home/user/project", True)
        assert store.get("/home/user/project") is True

    def test_returns_false_for_untrusted(self, tmp_path):
        store = _store(tmp_path)
        store.set("/home/user/dangerous", False)
        assert store.get("/home/user/dangerous") is False

    def test_inherits_parent_trust(self, tmp_path):
        store = _store(tmp_path)
        store.set("/home/user", True)
        assert store.get("/home/user/project/subdir") is True

    def test_child_overrides_parent(self, tmp_path):
        store = _store(tmp_path)
        store.set("/home/user", True)
        store.set("/home/user/restricted", False)
        assert store.get("/home/user/restricted") is False

    def test_parent_override_does_not_affect_unrelated(self, tmp_path):
        store = _store(tmp_path)
        store.set("/home/user/projectA", True)
        assert store.get("/home/user/projectB") is None


class TestTrustStoreSet:
    def test_set_persists_decision(self, tmp_path):
        store = _store(tmp_path)
        store.set("/my/project", True)
        store2 = _store(tmp_path)
        assert store2.get("/my/project") is True

    def test_set_none_removes_entry(self, tmp_path):
        store = _store(tmp_path)
        store.set("/my/project", True)
        store.set("/my/project", None)
        assert store.get("/my/project") is None

    def test_overwrite_decision(self, tmp_path):
        store = _store(tmp_path)
        store.set("/my/project", True)
        store.set("/my/project", False)
        assert store.get("/my/project") is False

    def test_creates_trust_json_file(self, tmp_path):
        store = _store(tmp_path)
        store.set("/some/path", True)
        assert (tmp_path / "trust.json").exists()

    def test_trust_json_does_not_contain_none(self, tmp_path):
        import json

        store = _store(tmp_path)
        store.set("/path/a", True)
        store.set("/path/b", None)
        data = json.loads((tmp_path / "trust.json").read_text())
        assert "/path/b" not in data


class TestTrustStoreGetStoredPath:
    def test_returns_exact_path_when_set(self, tmp_path):
        store = _store(tmp_path)
        store.set("/exact/path", True)
        result = store.get_stored_path("/exact/path")
        assert result is not None
        assert "exact" in result

    def test_returns_parent_path_when_inherited(self, tmp_path):
        store = _store(tmp_path)
        store.set("/parent", True)
        result = store.get_stored_path("/parent/child/deep")
        assert result is not None
        assert "parent" in result

    def test_returns_none_when_no_entry(self, tmp_path):
        store = _store(tmp_path)
        assert store.get_stored_path("/totally/unknown") is None


class TestTrustStoreApplyOption:
    def test_session_only_option_not_persisted(self, tmp_path):
        store = _store(tmp_path)
        option = TrustOption(label="This session only", trusted=True, save_path=None)
        store.apply_option(option)
        assert not (tmp_path / "trust.json").exists()

    def test_saved_option_persists(self, tmp_path):
        store = _store(tmp_path)
        option = TrustOption(label="Trust always", trusted=True, save_path="/my/project")
        store.apply_option(option)
        assert store.get("/my/project") is True

    def test_clear_child_path_removes_child(self, tmp_path):
        store = _store(tmp_path)
        store.set("/my/project/child", True)
        option = TrustOption(
            label="Trust parent",
            trusted=True,
            save_path="/my/project",
            clear_child_path="/my/project/child",
        )
        store.apply_option(option)
        store2 = _store(tmp_path)
        assert store2.get_stored_path("/my/project/child") == store2.get_stored_path("/my/project")
