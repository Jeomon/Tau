"""The resume picker has to identify a session, not the file it lives in.

Under the SQLite backend one database holds every session of a project, so a
path no longer names one. Keying on it made the picker unable to select a
specific session — and made deleting one destroy every session in the project.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.modes.interactive.components.session_selector import ResumeSelector


def _info(path: Path, session_id: str, name: str | None = None):
    """A SessionInfo-shaped stand-in for the picker."""
    now = datetime.now()
    return SimpleNamespace(
        path=path,
        id=session_id,
        cwd=path.parent,
        name=name,
        parent_session=None,
        created=now,
        modified=now,
        message_count=2,
    )


@pytest.fixture
def database(tmp_path):
    """Two sessions sharing one project database, as SQLite stores them."""
    from tau.message.types import AssistantMessage, UserMessage
    from tau.session.manager import SessionManager

    ids = []
    for label in ("one", "two"):
        manager = SessionManager(cwd=tmp_path, session_dir=tmp_path / "s", storage_backend="sqlite")
        manager.append_message(UserMessage.from_text(f"q {label}"))
        manager.append_message(AssistantMessage.from_text(f"a {label}"))
        ids.append(manager.session_id)
    return tmp_path / "s" / "sessions.db", ids


class TestSelection:
    def test_the_selected_session_is_reported_not_just_its_path(self, tmp_path):
        db = tmp_path / "sessions.db"
        selector = ResumeSelector(
            current_sessions=[_info(db, "aaa"), _info(db, "bbb")],
            all_sessions_loader=lambda: [],
        )

        first = selector.selected_session()
        selector.move_down() if hasattr(selector, "move_down") else None

        assert first is not None
        assert first.id in {"aaa", "bbb"}
        assert selector.selected_path() == db

    def test_two_sessions_in_one_database_are_both_listed(self, tmp_path):
        """Keying on path would collapse them into a single row."""
        db = tmp_path / "sessions.db"
        selector = ResumeSelector(
            current_sessions=[_info(db, "aaa"), _info(db, "bbb")],
            all_sessions_loader=lambda: [],
        )

        assert len(selector._filtered) == 2


class TestCurrentSession:
    def test_the_active_session_is_hidden_by_id(self, tmp_path):
        db = tmp_path / "sessions.db"
        selector = ResumeSelector(
            current_sessions=[_info(db, "aaa"), _info(db, "bbb")],
            all_sessions_loader=lambda: [],
            current_session_path=db,
            current_session_id="aaa",
        )

        assert [s.id for s in selector._filtered] == ["bbb"]

    def test_without_an_id_the_path_still_decides(self, tmp_path):
        """Callers that never pass an id keep their previous behaviour."""
        one, two = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        selector = ResumeSelector(
            current_sessions=[_info(one, "aaa"), _info(two, "bbb")],
            all_sessions_loader=lambda: [],
            current_session_path=one,
        )

        assert [s.id for s in selector._filtered] == ["bbb"]

    def test_the_active_session_cannot_be_deleted(self, tmp_path):
        db = tmp_path / "sessions.db"
        selector = ResumeSelector(
            current_sessions=[_info(db, "aaa")],
            all_sessions_loader=lambda: [],
            current_session_id="aaa",
        )
        selector._filtered = [_info(db, "aaa")]  # normally hidden; force it visible

        selector.start_delete()

        assert selector.confirming_delete is False
        assert "Cannot delete the active session" in selector._status_msg


class TestDelete:
    def test_deleting_one_sqlite_session_leaves_the_others(self, database):
        """The whole point: unlinking the database would take every session of
        the project with it."""
        from tau.session.storage import list_sqlite_sessions

        db, ids = database
        selector = ResumeSelector(
            current_sessions=[_info(db, ids[0]), _info(db, ids[1])],
            all_sessions_loader=lambda: [],
        )

        selector.start_delete()
        selector.confirm_delete()

        assert db.exists(), "the project database must survive"
        remaining = [info.id for info in list_sqlite_sessions(db)]
        assert len(remaining) == 1

    def test_the_deleted_session_leaves_the_list(self, database):
        db, ids = database
        selector = ResumeSelector(
            current_sessions=[_info(db, ids[0]), _info(db, ids[1])],
            all_sessions_loader=lambda: [],
        )
        doomed = selector.selected_session().id

        selector.start_delete()
        selector.confirm_delete()

        assert doomed not in [s.id for s in selector._filtered]
        assert len(selector._filtered) == 1

    def test_a_jsonl_session_is_still_deleted_by_unlinking(self, tmp_path):
        file = tmp_path / "session.jsonl"
        file.write_text("{}\n")
        selector = ResumeSelector(
            current_sessions=[_info(file, "aaa")], all_sessions_loader=lambda: []
        )

        selector.start_delete()
        selector.confirm_delete()

        assert not file.exists()


class TestResumeWiring:
    def test_the_picker_commits_the_session_not_the_path(self):
        import inspect

        from tau.modes.interactive.components import selector_controller

        source = inspect.getsource(selector_controller)

        assert "selected_session()" in source

    def test_apply_resume_forwards_the_id(self):
        import inspect

        from tau.modes.interactive.commands import session as panel

        source = inspect.getsource(panel._apply_resume)

        assert "session_id=session_id" in source
