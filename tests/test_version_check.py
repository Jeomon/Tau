"""Tests for tau/utils/version_check.py — PEP 440 version comparison."""

from __future__ import annotations

from tau.utils.version_check import _is_newer


class TestIsNewer:
    def test_newer_version_returns_true(self):
        assert _is_newer("2.0.0", "1.0.0") is True

    def test_older_version_returns_false(self):
        assert _is_newer("1.0.0", "2.0.0") is False

    def test_same_version_returns_false(self):
        assert _is_newer("1.0.0", "1.0.0") is False

    def test_patch_newer(self):
        assert _is_newer("1.0.1", "1.0.0") is True

    def test_patch_older(self):
        assert _is_newer("1.0.0", "1.0.1") is False

    def test_minor_newer(self):
        assert _is_newer("1.1.0", "1.0.9") is True

    def test_major_bump(self):
        assert _is_newer("2.0.0", "1.99.99") is True

    def test_pre_release_is_older_than_release(self):
        # PEP 440: 1.0.0rc1 < 1.0.0
        assert _is_newer("1.0.0", "1.0.0rc1") is True

    def test_release_candidate_not_newer_than_release(self):
        assert _is_newer("1.0.0rc1", "1.0.0") is False

    def test_dev_release_is_older(self):
        # 1.0.0.dev1 < 1.0.0
        assert _is_newer("1.0.0", "1.0.0.dev1") is True

    def test_post_release_is_newer(self):
        # 1.0.0.post1 > 1.0.0
        assert _is_newer("1.0.0.post1", "1.0.0") is True

    def test_multi_digit_version(self):
        assert _is_newer("1.10.0", "1.9.0") is True

    def test_fallback_with_non_standard(self):
        # Falls back to naive compare — should not raise
        result = _is_newer("2.0", "1.0")
        assert result is True
