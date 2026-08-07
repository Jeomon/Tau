"""Selecting the SQLite session backend.

`SQLiteSessionStorage` was complete and unit-tested from the start, but nothing
in production ever constructed it: `SessionManager` built only the in-memory or
file backends and no setting selected otherwise. These cover the wiring that
makes it reachable.
"""

from __future__ import annotations

from tau.message.types import AssistantMessage, UserMessage
from tau.session.manager import SessionManager
from tau.session.storage import (
    FileSessionStorage,
    InMemorySessionStorage,
    SQLiteSessionStorage,
)


def _talk(manager: SessionManager, text: str) -> None:
    """A full exchange. Nothing is persisted before the first assistant reply."""
    manager.append_message(UserMessage.from_text(f"question {text}"))
    manager.append_message(AssistantMessage.from_text(f"answer {text}"))


class TestSelection:
    def test_the_file_backend_stays_the_default(self, tmp_path):
        manager = SessionManager(cwd=tmp_path, session_dir=tmp_path / "s")

        assert manager.storage_backend == "file"
        assert isinstance(manager._storage, FileSessionStorage)
        assert manager.session_file is not None
        assert manager.session_file.suffix == ".jsonl"

    def test_sqlite_is_selected_explicitly(self, tmp_path):
        manager = SessionManager(cwd=tmp_path, session_dir=tmp_path / "s", storage_backend="sqlite")

        assert isinstance(manager._storage, SQLiteSessionStorage)
        assert manager.session_file is not None
        assert manager.session_file.name == "sessions.db"

    def test_a_non_persisting_session_stays_in_memory(self, tmp_path):
        manager = SessionManager(
            cwd=tmp_path, session_dir=tmp_path / "s", persist=False, storage_backend="sqlite"
        )

        assert isinstance(manager._storage, InMemorySessionStorage)

    def test_the_setting_defaults_to_file(self):
        from tau.settings.manager import SettingsManager
        from tau.settings.types import Settings

        manager = SettingsManager.__new__(SettingsManager)
        manager.settings = Settings()  # type: ignore[attr-defined]

        assert manager.get_session_storage() == "file"


class TestStorage:
    def test_a_session_round_trips_through_the_database(self, tmp_path):
        manager = SessionManager(cwd=tmp_path, session_dir=tmp_path / "s", storage_backend="sqlite")
        _talk(manager, "one")
        session_id = manager.session_id

        reopened = SessionManager(
            cwd=tmp_path,
            session_dir=tmp_path / "s",
            session_file=manager.session_file,
            storage_backend="sqlite",
            session_id=session_id,
        )

        assert reopened.session_id == session_id
        assert len(reopened.get_entries()) == len(manager.get_entries())

    def test_every_session_of_a_project_shares_one_database(self, tmp_path):
        """The point of the backend: listing becomes one indexed query rather
        than a full read of every session file."""
        paths = set()
        for label in ("one", "two", "three"):
            manager = SessionManager(
                cwd=tmp_path, session_dir=tmp_path / "s", storage_backend="sqlite"
            )
            _talk(manager, label)
            paths.add(manager.session_file)

        assert len(paths) == 1
        assert not list((tmp_path / "s").glob("*.jsonl"))

    def test_sessions_created_together_stay_distinct(self, tmp_path):
        """Rows are keyed by session id, so ids colliding would silently merge
        two sessions — which a file per session would have masked."""
        ids = set()
        for label in ("one", "two", "three"):
            manager = SessionManager(
                cwd=tmp_path, session_dir=tmp_path / "s", storage_backend="sqlite"
            )
            _talk(manager, label)
            ids.add(manager.session_id)

        assert len(ids) == 3


class TestListing:
    def test_sqlite_sessions_are_listed(self, tmp_path):
        for label in ("one", "two"):
            manager = SessionManager(
                cwd=tmp_path, session_dir=tmp_path / "s", storage_backend="sqlite"
            )
            _talk(manager, label)

        listed = SessionManager.list(tmp_path, session_dir=tmp_path / "s")

        assert len(listed) == 2
        assert all(info.message_count == 2 for info in listed)

    def test_a_project_that_switched_backends_lists_both(self, tmp_path):
        """Switching the setting must not hide the history written before it."""
        for backend in ("sqlite", "file"):
            manager = SessionManager(
                cwd=tmp_path, session_dir=tmp_path / "s", storage_backend=backend
            )
            _talk(manager, backend)

        listed = SessionManager.list(tmp_path, session_dir=tmp_path / "s")

        assert {info.path.suffix for info in listed} == {".db", ".jsonl"}

    def test_listing_survives_an_unreadable_database(self, tmp_path):
        """A corrupt database must not take the whole listing down with it."""
        directory = tmp_path / "s"
        manager = SessionManager(cwd=tmp_path, session_dir=directory, storage_backend="file")
        _talk(manager, "jsonl")
        (directory / "sessions.db").write_bytes(b"not a database")

        listed = SessionManager.list(tmp_path, session_dir=directory)

        assert len(listed) == 1


class TestResumeWiring:
    def test_resume_carries_the_session_id(self):
        """One database holds every session of a project, so a path alone does
        not identify which one to open."""
        import inspect

        from tau.runtime.service import Runtime

        signature = inspect.signature(Runtime.resume_session)

        assert "session_id" in signature.parameters

    def test_the_runtime_config_carries_it_too(self):
        from tau.runtime.types import RuntimeConfig

        assert "session_id" in RuntimeConfig.model_fields
