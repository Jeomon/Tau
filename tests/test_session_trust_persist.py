"""Tests for trust-gated session persistence.

Verifies that the session directory is NOT created on disk until the user
grants project trust, and IS created (with buffered entries flushed) once
enable_persist() is called.
"""

from __future__ import annotations

from pathlib import Path

from tau.session.manager import SessionManager


def _manager(tmp_path: Path, persist: bool) -> SessionManager:
    """Create a SessionManager with an isolated session dir under tmp_path."""
    session_dir = tmp_path / "sessions"
    return SessionManager(cwd=tmp_path, session_dir=session_dir, persist=persist)


# ---------------------------------------------------------------------------
# SessionManager.enable_persist()
# ---------------------------------------------------------------------------


class TestEnablePersist:
    def test_no_dir_created_when_persist_false(self, tmp_path):
        """Session directory must not exist when persist=False."""
        session_dir = tmp_path / "sessions"
        _manager(tmp_path, persist=False)
        assert not session_dir.exists()

    def test_persist_true_creates_dir_immediately(self, tmp_path):
        """Baseline: persist=True creates the directory on construction."""
        session_dir = tmp_path / "sessions"
        _manager(tmp_path, persist=True)
        assert session_dir.exists()

    def test_enable_persist_creates_dir(self, tmp_path):
        """enable_persist() must create the session directory."""
        session_dir = tmp_path / "sessions"
        sm = _manager(tmp_path, persist=False)
        assert not session_dir.exists()

        sm.enable_persist()

        assert session_dir.exists()

    def test_enable_persist_writes_session_file(self, tmp_path):
        """enable_persist() must flush buffered entries to a .jsonl file."""
        sm = _manager(tmp_path, persist=False)
        assert sm.session_file is None

        sm.enable_persist()

        assert sm.session_file is not None
        assert sm.session_file.exists()
        # At minimum the SessionHeader line must be present
        content = sm.session_file.read_text()
        assert content.strip()
        assert '"type":"session"' in content

    def test_enable_persist_sets_persist_flag(self, tmp_path):
        sm = _manager(tmp_path, persist=False)
        assert not sm.persist
        sm.enable_persist()
        assert sm.persist

    def test_enable_persist_idempotent(self, tmp_path):
        """Calling enable_persist() twice must not raise or duplicate files."""
        sm = _manager(tmp_path, persist=False)
        sm.enable_persist()
        first_file = sm.session_file

        sm.enable_persist()

        assert sm.session_file == first_file

    def test_enable_persist_on_already_persisting_manager_is_noop(self, tmp_path):
        """enable_persist() on an already-persisting manager leaves file unchanged."""
        sm = _manager(tmp_path, persist=True)
        original_file = sm.session_file

        sm.enable_persist()

        assert sm.session_file == original_file

    def test_pre_enable_entries_flushed(self, tmp_path):
        """Entries buffered before enable_persist() appear in the flushed file."""
        sm = _manager(tmp_path, persist=False)
        sm.append_custom_info("test:marker", {"x": 1})

        sm.enable_persist()

        text = sm.session_file.read_text()
        assert "test:marker" in text

    def test_session_id_preserved_after_enable(self, tmp_path):
        """The session ID generated before trust approval must not change."""
        sm = _manager(tmp_path, persist=False)
        original_id = sm.session_id

        sm.enable_persist()

        assert sm.session_id == original_id
        assert original_id in sm.session_file.name


# ---------------------------------------------------------------------------
# Trust-pending → persist=False path
#
# We test at the SessionManager level rather than wiring through the full
# async RuntimeContext.create() to keep the tests fast and the mocking
# surface small. The trust-pending path in types.py passes persist=False
# to SessionManager — these tests verify that contract holds.
# ---------------------------------------------------------------------------


class TestTrustPendingPersistFlag:
    """persist=False is the contract the trust-pending path must satisfy."""

    def test_persist_false_no_mkdir(self, tmp_path):
        """When persist=False (trust pending), no directories are created."""
        session_dir = tmp_path / "sessions"
        SessionManager(cwd=tmp_path, session_dir=session_dir, persist=False)
        assert not session_dir.exists()

    def test_persist_false_no_session_file(self, tmp_path):
        """When persist=False, session_file must remain None until enable_persist()."""
        sm = SessionManager(cwd=tmp_path, session_dir=tmp_path / "sessions", persist=False)
        assert sm.session_file is None

    def test_persist_false_then_approve_creates_dir(self, tmp_path):
        """Simulates the user approving trust: enable_persist() unblocks persistence."""
        session_dir = tmp_path / "sessions"
        sm = SessionManager(cwd=tmp_path, session_dir=session_dir, persist=False)
        assert not session_dir.exists()

        # User approves trust — app calls enable_persist()
        sm.enable_persist()

        assert session_dir.exists()
        assert sm.session_file is not None and sm.session_file.exists()

    def test_persist_false_then_decline_never_creates_dir(self, tmp_path):
        """Simulates the user declining trust: enable_persist() is never called."""
        session_dir = tmp_path / "sessions"
        sm = SessionManager(cwd=tmp_path, session_dir=session_dir, persist=False)

        # User declines — app exits, enable_persist() is never called.
        # Directory must not have been created at any point.
        assert not session_dir.exists()
        assert sm.session_file is None
