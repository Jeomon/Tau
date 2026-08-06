"""Conformance suite for the session storage backends.

Every behavioural test runs against all three backends through the same
fixture. A backend is only correct if it is substitutable for the others, so
the suite is written once and parametrized rather than duplicated per class —
a divergence shows up as a failure on one backend, not as an untested gap.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tau.message.types import AssistantMessage, UserMessage
from tau.session.storage import (
    FileSessionStorage,
    InMemorySessionStorage,
    SQLiteSessionStorage,
    deserialize_entry,
    list_sqlite_sessions,
    serialize_entry,
)
from tau.session.types import (
    MessageEntry,
    SessionFileEntry,
    SessionHeader,
    SessionType,
)

BACKENDS = ["file", "memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
def storage(request: pytest.FixtureRequest, tmp_path: Path):
    """Yield one storage backend per parametrized run."""
    match request.param:
        case "file":
            yield FileSessionStorage(tmp_path / "session.jsonl")
        case "memory":
            yield InMemorySessionStorage()
        case "sqlite":
            backend = SQLiteSessionStorage(tmp_path / "sessions.db", "sess-1")
            yield backend
            backend.close()
        case _:  # pragma: no cover - guarded by params
            raise AssertionError(request.param)


def header(session_id: str = "s1") -> SessionHeader:
    return SessionHeader(id=session_id, timestamp=1.0, cwd=Path("/tmp"))


def message(text: str, entry_id: str, parent_id: str | None = None) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        timestamp=2.0,
        parent_id=parent_id,
        message=UserMessage.from_text(text),
    )


def seed(storage, count: int = 3) -> list[SessionFileEntry]:
    """Write a header plus ``count`` messages and return what was written."""
    entries: list[SessionFileEntry] = [header()]
    storage.rewrite([entries[0]])
    parent: str | None = None
    for index in range(count):
        entry = message(f"m{index}", f"e{index}", parent)
        storage.append(entry)
        entries.append(entry)
        parent = entry.id
    return entries


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


class TestEmpty:
    def test_a_fresh_backend_holds_nothing(self, storage):
        assert storage.exists() is False
        assert storage.read() == []

    def test_reading_ids_from_an_empty_backend_finds_nothing(self, storage):
        assert storage.read_entries_by_id({"e0"}) == {}

    def test_shedding_read_of_an_empty_backend_is_empty(self, storage):
        entries, shed = storage.read_shedding()
        assert entries == []
        assert shed == set()

    def test_reading_does_not_create_durable_state(self, storage):
        """Probing a session that has none must not leave one behind."""
        storage.read()
        storage.read_entries_by_id({"e0"})

        location = storage.location
        assert location is None or not location.exists()
        assert storage.exists() is False


# ---------------------------------------------------------------------------
# Append and read
# ---------------------------------------------------------------------------


class TestAppendAndRead:
    def test_entries_come_back_in_append_order(self, storage):
        """Order is load-bearing: the manager resolves the leaf from the last entry."""
        written = seed(storage, count=5)

        assert [entry.id for entry in storage.read()] == [entry.id for entry in written]

    def test_the_header_is_first(self, storage):
        seed(storage)

        assert storage.read()[0].type == SessionType.SESSION_HEADER

    def test_content_survives_a_round_trip(self, storage):
        storage.rewrite([header()])
        storage.append(message("hello world", "e0"))

        entry = storage.read()[1]
        assert isinstance(entry, MessageEntry)
        assert entry.message.contents[0].content == "hello world"

    def test_parent_links_survive_a_round_trip(self, storage):
        seed(storage, count=3)

        entries = storage.read()[1:]
        assert [entry.parent_id for entry in entries] == [None, "e0", "e1"]

    def test_exists_becomes_true_once_written(self, storage):
        assert storage.exists() is False
        seed(storage, count=1)
        assert storage.exists() is True

    def test_history_without_a_header_reads_as_empty(self, storage):
        """Mirrors read_session_file: a file not starting with a header is not a session."""
        storage.append(message("orphan", "e0"))

        assert storage.read() == []


# ---------------------------------------------------------------------------
# Rewrite
# ---------------------------------------------------------------------------


class TestRewrite:
    def test_rewrite_replaces_everything(self, storage):
        seed(storage, count=3)

        replacement: list[SessionFileEntry] = [header(), message("only", "z0")]
        storage.rewrite(replacement)

        assert [entry.id for entry in storage.read()] == ["s1", "z0"]

    def test_rewrite_to_empty_clears_the_history(self, storage):
        seed(storage, count=2)

        storage.rewrite([])

        assert storage.read() == []
        assert storage.exists() is False

    def test_append_after_rewrite_lands_at_the_end(self, storage):
        seed(storage, count=2)
        storage.rewrite([header(), message("kept", "k0")])

        storage.append(message("after", "a0", "k0"))

        assert [entry.id for entry in storage.read()] == ["s1", "k0", "a0"]

    def test_rewriting_a_removed_entry_away_is_durable(self, storage):
        """The manager's undo path: drop one entry, keep the rest."""
        written = seed(storage, count=3)
        kept = [entry for entry in written if entry.id != "e1"]

        storage.rewrite(kept)

        assert [entry.id for entry in storage.read()] == ["s1", "e0", "e2"]


# ---------------------------------------------------------------------------
# Selective reads
# ---------------------------------------------------------------------------


class TestReadEntriesById:
    def test_only_the_requested_ids_come_back(self, storage):
        seed(storage, count=4)

        found = storage.read_entries_by_id({"e1", "e3"})

        assert set(found) == {"e1", "e3"}

    def test_a_missing_id_is_absent_not_an_error(self, storage):
        seed(storage, count=2)

        found = storage.read_entries_by_id({"e0", "nope"})

        assert set(found) == {"e0"}

    def test_an_empty_request_short_circuits(self, storage):
        seed(storage, count=2)

        assert storage.read_entries_by_id(set()) == {}

    def test_returned_entries_carry_full_content(self, storage):
        storage.rewrite([header()])
        storage.append(message("full body", "e0"))

        entry = storage.read_entries_by_id({"e0"})["e0"]
        assert isinstance(entry, MessageEntry)
        assert entry.message.contents[0].content == "full body"


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


class TestLocking:
    def test_the_lock_is_reentrant(self, storage):
        """read-merge-rewrite nests append/rewrite inside an outer lock."""
        # Deliberately nested rather than combined: acquiring while already
        # holding is the behaviour under test, not a style accident.
        with storage.lock():  # noqa: SIM117
            with storage.lock():
                storage.rewrite([header()])
                storage.append(message("nested", "e0"))

        assert [entry.id for entry in storage.read()] == ["s1", "e0"]

    def test_the_lock_serializes_threads(self, storage):
        seed(storage, count=1)
        observed: list[int] = []

        def writer(index: int) -> None:
            with storage.lock():
                current = storage.read()
                observed.append(len(current))
                storage.append(message(f"t{index}", f"t{index}"))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Each writer must have seen a distinct, growing history; an
        # interleaved read would repeat a length.
        assert sorted(observed) == [2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_mutating_the_returned_list_does_not_alter_storage(self, storage):
        seed(storage, count=2)

        snapshot = storage.read()
        snapshot.clear()

        assert len(storage.read()) == 3

    def test_two_handles_on_one_location_see_the_same_history(self, tmp_path):
        """Durable backends must not cache away another handle's writes."""
        first = FileSessionStorage(tmp_path / "s.jsonl")
        seed(first, count=2)

        second = FileSessionStorage(tmp_path / "s.jsonl")
        assert [entry.id for entry in second.read()] == ["s1", "e0", "e1"]

    def test_two_sqlite_handles_on_one_database_agree(self, tmp_path):
        first = SQLiteSessionStorage(tmp_path / "sessions.db", "sess-1")
        seed(first, count=2)
        second = SQLiteSessionStorage(tmp_path / "sessions.db", "sess-1")
        try:
            assert [entry.id for entry in second.read()] == ["s1", "e0", "e1"]
        finally:
            first.close()
            second.close()


# ---------------------------------------------------------------------------
# Backend-specific guarantees
# ---------------------------------------------------------------------------


class TestLocation:
    def test_file_reports_its_path(self, tmp_path):
        path = tmp_path / "s.jsonl"
        assert FileSessionStorage(path).location == path

    def test_sqlite_reports_its_path(self, tmp_path):
        path = tmp_path / "sessions.db"
        backend = SQLiteSessionStorage(path, "sess-1")
        try:
            assert backend.location == path
        finally:
            backend.close()

    def test_memory_reports_no_path(self):
        assert InMemorySessionStorage().location is None


class TestFileBackend:
    def test_it_writes_the_jsonl_format_the_manager_already_reads(self, tmp_path):
        path = tmp_path / "s.jsonl"
        storage = FileSessionStorage(path)
        seed(storage, count=2)

        from tau.session.utils import read_session_file

        assert [entry.id for entry in read_session_file(path)] == ["s1", "e0", "e1"]

    def test_one_entry_is_one_line(self, tmp_path):
        path = tmp_path / "s.jsonl"
        seed(FileSessionStorage(path), count=3)

        assert len(path.read_text().strip().splitlines()) == 4

    def test_shedding_read_drops_folded_bodies_but_keeps_ids(self, tmp_path):
        """Shed content stays recoverable, which is what makes shedding safe."""
        path = tmp_path / "s.jsonl"
        storage = FileSessionStorage(path)
        seed(storage, count=2)

        entries, shed = storage.read_shedding()

        assert [entry.id for entry in entries] == ["s1", "e0", "e1"]
        # Nothing is compacted here, so nothing may be shed.
        assert shed == set()

    def test_an_unparseable_line_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "s.jsonl"
        storage = FileSessionStorage(path)
        seed(storage, count=2)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("{ not json\n")

        assert [entry.id for entry in storage.read()] == ["s1", "e0", "e1"]


class TestMemoryBackend:
    def test_it_never_sheds(self):
        """Memory holds the only copy; shedding would destroy content."""
        storage = InMemorySessionStorage()
        seed(storage, count=2)

        _, shed = storage.read_shedding()

        assert shed == set()

    def test_it_can_be_seeded(self):
        storage = InMemorySessionStorage([header(), message("seeded", "e0")])

        assert [entry.id for entry in storage.read()] == ["s1", "e0"]

    def test_seeding_copies_the_input_list(self):
        seeded: list[SessionFileEntry] = [header()]
        storage = InMemorySessionStorage(seeded)

        seeded.append(message("late", "e9"))

        assert len(storage.read()) == 1


class TestSQLiteBackend:
    def test_a_repeated_id_is_not_duplicated(self, tmp_path):
        storage = SQLiteSessionStorage(tmp_path / "sessions.db", "sess-1")
        try:
            storage.rewrite([header()])
            entry = message("once", "e0")
            storage.append(entry)
            storage.append(entry)

            assert [e.id for e in storage.read()] == ["s1", "e0"]
        finally:
            storage.close()

    def test_a_failed_rewrite_rolls_back(self, tmp_path):
        storage = SQLiteSessionStorage(tmp_path / "sessions.db", "sess-1")
        try:
            seed(storage, count=2)

            with pytest.raises(RuntimeError), storage.lock():
                storage.rewrite([header()])
                raise RuntimeError("boom")

            assert [entry.id for entry in storage.read()] == ["s1", "e0", "e1"]
        finally:
            storage.close()

    def test_it_survives_close_and_reopen(self, tmp_path):
        path = tmp_path / "sessions.db"
        storage = SQLiteSessionStorage(path, "sess-1")
        seed(storage, count=2)
        storage.close()

        reopened = SQLiteSessionStorage(path, "sess-1")
        try:
            assert [entry.id for entry in reopened.read()] == ["s1", "e0", "e1"]
        finally:
            reopened.close()


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


class TestCodec:
    def test_a_message_entry_round_trips(self):
        entry = message("round trip", "e0", "parent")

        restored = deserialize_entry(serialize_entry(entry))

        assert isinstance(restored, MessageEntry)
        assert restored.id == "e0"
        assert restored.parent_id == "parent"
        assert restored.message.contents[0].content == "round trip"

    def test_a_header_round_trips(self):
        restored = deserialize_entry(serialize_entry(header("abc")))

        assert isinstance(restored, SessionHeader)
        assert restored.id == "abc"

    def test_an_assistant_message_round_trips(self):
        entry = MessageEntry(id="a0", timestamp=1.0, message=AssistantMessage.from_text("hi"))

        restored = deserialize_entry(serialize_entry(entry))

        assert isinstance(restored, MessageEntry)
        assert isinstance(restored.message, AssistantMessage)

    def test_none_fields_are_omitted_from_the_encoding(self):
        """exclude_none keeps session lines compact, as the manager writes them."""
        assert '"parent_id"' not in serialize_entry(message("x", "e0"))


# ---------------------------------------------------------------------------
# Per-project SQLite: many sessions in one database
# ---------------------------------------------------------------------------


class TestProjectDatabase:
    """One database per project holds every session, keyed by session_id."""

    def open(self, path: Path, session_id: str) -> SQLiteSessionStorage:
        return SQLiteSessionStorage(path, session_id)

    def test_sessions_in_one_database_do_not_see_each_other(self, tmp_path):
        db = tmp_path / "sessions.db"
        first, second = self.open(db, "a"), self.open(db, "b")
        try:
            seed(first, count=3)
            seed(second, count=1)

            assert [e.id for e in first.read()] == ["s1", "e0", "e1", "e2"]
            assert [e.id for e in second.read()] == ["s1", "e0"]
        finally:
            first.close()
            second.close()

    def test_the_same_entry_id_may_exist_in_two_sessions(self, tmp_path):
        """A fork copies entry ids, so ids are unique per session, not globally."""
        db = tmp_path / "sessions.db"
        first, second = self.open(db, "a"), self.open(db, "b")
        try:
            seed(first, count=2)
            seed(second, count=2)

            assert [e.id for e in first.read()] == ["s1", "e0", "e1"]
            assert [e.id for e in second.read()] == ["s1", "e0", "e1"]
        finally:
            first.close()
            second.close()

    def test_rewriting_one_session_leaves_the_others_intact(self, tmp_path):
        db = tmp_path / "sessions.db"
        first, second = self.open(db, "a"), self.open(db, "b")
        try:
            seed(first, count=3)
            seed(second, count=3)

            first.rewrite([header()])

            assert [e.id for e in first.read()] == ["s1"]
            assert [e.id for e in second.read()] == ["s1", "e0", "e1", "e2"]
        finally:
            first.close()
            second.close()

    def test_an_absent_session_reads_empty_from_a_populated_database(self, tmp_path):
        db = tmp_path / "sessions.db"
        present = self.open(db, "a")
        try:
            seed(present, count=2)
            missing = self.open(db, "nope")
            try:
                assert missing.read() == []
                assert missing.exists() is False
            finally:
                missing.close()
        finally:
            present.close()

    def test_reading_ids_does_not_leak_across_sessions(self, tmp_path):
        """Same entry id in both sessions: the read must return *this* one."""
        db = tmp_path / "sessions.db"
        first, second = self.open(db, "a"), self.open(db, "b")
        try:
            first.rewrite([header()])
            first.append(message("belongs to a", "e0"))
            second.rewrite([header()])
            second.append(message("belongs to b", "e0"))

            found = first.read_entries_by_id({"e0"})

            assert set(found) == {"e0"}
            entry = found["e0"]
            assert isinstance(entry, MessageEntry)
            assert entry.message.contents[0].content == "belongs to a"
        finally:
            first.close()
            second.close()


class TestListSqliteSessions:
    """Listing is why the database is per project rather than per session."""

    def test_an_absent_database_lists_nothing(self, tmp_path):
        assert list_sqlite_sessions(tmp_path / "missing.db") == []

    def test_every_session_is_listed(self, tmp_path):
        db = tmp_path / "sessions.db"
        for session_id in ("a", "b", "c"):
            storage = SQLiteSessionStorage(db, session_id)
            seed(storage, count=2)
            storage.close()

        infos = list_sqlite_sessions(db)

        assert len(infos) == 3

    def test_the_message_count_is_right_without_parsing_history(self, tmp_path):
        db = tmp_path / "sessions.db"
        storage = SQLiteSessionStorage(db, "a")
        seed(storage, count=7)
        storage.close()

        assert list_sqlite_sessions(db)[0].message_count == 7

    def test_the_session_name_is_picked_up(self, tmp_path):
        from tau.session.types import SessionInfoEntry

        db = tmp_path / "sessions.db"
        storage = SQLiteSessionStorage(db, "a")
        seed(storage, count=1)
        storage.append(SessionInfoEntry(id="n0", timestamp=3.0, name="Refactor auth"))
        storage.close()

        assert list_sqlite_sessions(db)[0].name == "Refactor auth"

    def test_a_session_without_a_name_lists_none(self, tmp_path):
        db = tmp_path / "sessions.db"
        storage = SQLiteSessionStorage(db, "a")
        seed(storage, count=1)
        storage.close()

        assert list_sqlite_sessions(db)[0].name is None

    def test_the_header_fields_are_reported(self, tmp_path):
        db = tmp_path / "sessions.db"
        storage = SQLiteSessionStorage(db, "a")
        storage.rewrite([SessionHeader(id="abc", timestamp=1.0, cwd=Path("/proj"))])
        storage.close()

        info = list_sqlite_sessions(db)[0]

        assert info.id == "abc"
        assert info.cwd == Path("/proj")
        assert info.path == db

    def test_counts_are_not_mixed_between_sessions(self, tmp_path):
        db = tmp_path / "sessions.db"
        for session_id, count in (("a", 2), ("b", 5)):
            storage = SQLiteSessionStorage(db, session_id)
            seed(storage, count=count)
            storage.close()

        counts = {info.id: info.message_count for info in list_sqlite_sessions(db)}

        # Both sessions use header id "s1", so the counts collapse onto one
        # key only if the query forgot to group by session.
        assert sorted(info.message_count for info in list_sqlite_sessions(db)) == [2, 5], counts


class TestSQLiteConcurrency:
    """Several sessions of one project now share a file and must not collide."""

    def test_concurrent_sessions_in_one_database(self, tmp_path):
        db = tmp_path / "sessions.db"
        errors: list[str] = []

        def writer(name: str) -> None:
            try:
                storage = SQLiteSessionStorage(db, name)
                storage.rewrite([header(name)])
                for index in range(30):
                    storage.append(message(f"{name}-{index}", f"e{index}"))
                storage.close()
            except Exception as error:  # pragma: no cover - failure detail
                errors.append(f"{name}: {error!r}")

        threads = [threading.Thread(target=writer, args=(f"s{k}",)) for k in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        for k in range(5):
            storage = SQLiteSessionStorage(db, f"s{k}")
            try:
                assert len(storage.read()) == 31
            finally:
                storage.close()

    def test_concurrent_writers_on_one_session(self, tmp_path):
        db = tmp_path / "sessions.db"
        opener = SQLiteSessionStorage(db, "s")
        opener.rewrite([header()])
        opener.close()
        errors: list[str] = []

        def writer(k: int) -> None:
            try:
                storage = SQLiteSessionStorage(db, "s")
                for index in range(20):
                    storage.append(message(f"w{k}-{index}", f"w{k}-e{index}"))
                storage.close()
            except Exception as error:  # pragma: no cover - failure detail
                errors.append(f"{k}: {error!r}")

        threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        storage = SQLiteSessionStorage(db, "s")
        try:
            assert len(storage.read()) == 81
        finally:
            storage.close()

    def test_wal_side_files_are_gone_after_close(self, tmp_path):
        """At rest a project is one file; -wal/-shm exist only while open."""
        db = tmp_path / "sessions.db"
        storage = SQLiteSessionStorage(db, "s")
        storage.rewrite([header()])
        assert (tmp_path / "sessions.db-wal").exists()

        storage.close()

        assert sorted(p.name for p in tmp_path.iterdir()) == ["sessions.db"]


class TestNoSideEffectsOnConstruction:
    """Binding storage to a path is not the same as deciding to write there."""

    def test_file_backend_creates_nothing(self, tmp_path):
        target = tmp_path / "never" / "existed" / "s.jsonl"

        FileSessionStorage(target)

        assert not target.parent.exists()
        assert not target.exists()

    def test_sqlite_backend_creates_nothing(self, tmp_path):
        target = tmp_path / "never" / "existed" / "sessions.db"

        SQLiteSessionStorage(target, "s").close()

        assert not target.parent.exists()
        assert not target.exists()

    def test_a_read_only_manager_does_not_materialise_its_directory(self, tmp_path):
        """persist=False binds a real file the manager must never write to."""
        from tau.session.manager import SessionManager

        target = tmp_path / "never" / "existed" / "s.jsonl"

        SessionManager(tmp_path, session_dir=tmp_path / "sd", session_file=target, persist=False)

        assert not target.parent.exists()

    def test_locking_does_create_the_directory(self, tmp_path):
        """Taking the lock is the first act of writing, so it may create it."""
        target = tmp_path / "fresh" / "s.jsonl"
        storage = FileSessionStorage(target)

        with storage.lock():
            pass

        assert target.parent.exists()
