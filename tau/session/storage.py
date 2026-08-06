"""Pluggable storage backends for one session's durable history.

A backend owns *how* a session's entries are persisted and nothing else. It
does not know about branches, leaves, compaction, shedding policy, or merge
rules — those stay in :class:`~tau.session.manager.SessionManager`, which is
the only component allowed to decide *what* to write.

The surface mirrors what the manager actually does to disk today:

======================  =========================================
manager operation       backend call
======================  =========================================
``_session_lock()``     :meth:`SessionStorage.lock`
``_merged_durable...``  :meth:`SessionStorage.read`
``_append_locked...``   :meth:`SessionStorage.append`
``_rewrite_file()``     :meth:`SessionStorage.rewrite`
``set_session()``       :meth:`SessionStorage.read_shedding`
``read_entries_by_id``  :meth:`SessionStorage.read_entries_by_id`
======================  =========================================

Ordering is part of the contract: :meth:`read` returns entries in the order
they were appended, header first. Session history is a chronological log and
the manager's index build (`_build_index`) resolves the current leaf by taking
the *last* entry it sees, so a backend that reorders silently relocates the
user's conversation.

Locks are reentrant. The manager performs read-merge-rewrite as one critical
section, and :meth:`append` / :meth:`rewrite` also lock internally, so the
nested acquisition must not deadlock.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from tau.session.types import (
    SessionEntry,
    SessionFileEntry,
    SessionHeader,
    SessionInfo,
    SessionType,
)

# _SESSION_FILE_ENTRY_ADAPTER is deliberately shared with tau.session.utils
# rather than rebuilt here: the discriminated union over ~10 entry models
# costs ~60ms of pydantic core-schema construction, and the models set
# defer_build precisely to pay that once, on first real use. A second
# TypeAdapter would pay it twice.
from tau.session.utils import (
    _SESSION_FILE_ENTRY_ADAPTER as _ENTRY_ADAPTER,
)
from tau.session.utils import (
    read_entries_by_id,
    read_session_file,
    read_session_file_shedding,
)
from tau.utils.fs import atomic_write_text

_log = logging.getLogger(__name__)

# What lock() hands back. FileLock, threading.RLock and a @contextmanager
# generator share no base class, so the contract is the protocol they do have
# in common: an exclusive-access context manager.
SessionLock = AbstractContextManager[Any]


def serialize_entry(entry: SessionFileEntry) -> str:
    """Encode one entry as the single JSON line the session format stores."""
    return entry.model_dump_json(exclude_none=True)


def deserialize_entry(payload: str) -> SessionFileEntry:
    """Decode one stored JSON line back into an entry model."""
    return _ENTRY_ADAPTER.validate_json(payload)


class SessionStorage(ABC):
    """Abstract storage backend for one session's durable history."""

    @property
    @abstractmethod
    def location(self) -> Path | None:
        """Filesystem path backing this session, or None when memory-only."""

    @abstractmethod
    def exists(self) -> bool:
        """Whether durable state for this session exists yet."""

    @abstractmethod
    def lock(self) -> SessionLock:
        """Return a reentrant exclusive-access context manager.

        Held across read-merge-rewrite so a concurrent writer cannot commit
        between the read and the replacement.
        """

    @abstractmethod
    def read(self) -> list[SessionFileEntry]:
        """Return every entry, in append order, header first.

        Returns an empty list when nothing is stored, and — matching
        :func:`~tau.session.utils.read_session_file` — when the stored data
        does not begin with a session header.
        """

    @abstractmethod
    def append(self, entry: SessionEntry) -> None:
        """Append one entry to the end of the history.

        A pure append never rewrites or drops what is already stored, so it
        stays safe with an arbitrarily stale in-memory view. That is why the
        manager prefers it: a full rewrite per entry is O(session size) per
        call and makes building a session O(n^2).
        """

    @abstractmethod
    def rewrite(self, entries: Sequence[SessionFileEntry]) -> None:
        """Atomically replace the entire history.

        Used only for mutations a pure append cannot express: removing an
        entry, or creating a branched session.
        """

    def read_shedding(self) -> tuple[list[SessionFileEntry], set[str]]:
        """Return entries plus the ids whose heavy content was left unread.

        The default is the honest one: read everything, shed nothing. A
        backend overrides this only when it can skip building the bodies of
        messages already folded into a compaction summary — an optimization
        that is only *safe* when the full content provably remains recoverable
        from durable storage, since the manager rehydrates through
        :meth:`read_entries_by_id`.
        """
        return self.read(), set()

    def read_entries_by_id(self, ids: set[str]) -> dict[str, SessionFileEntry]:
        """Return ``{id: entry}`` for the requested ids that are present.

        The rehydration counterpart of :meth:`read_shedding`. Missing or
        unparseable ids are simply absent; callers fall back to their resident
        copy.
        """
        if not ids:
            return {}
        return {entry.id: entry for entry in self.read() if entry.id in ids}


class _ReentrantFileLock:
    """A cross-process file lock that is also reentrant within a thread.

    ``FileLock`` alone is not enough. A fresh ``FileLock`` instance per
    acquisition raises ``RuntimeError: Deadlock`` the moment one is taken
    while another instance for the same path is held in the same thread —
    which is exactly what happens when :meth:`SessionStorage.rewrite` (which
    locks) is called inside a caller's ``with storage.lock():`` block.

    So the file lock is taken once, at depth zero, and the reentrancy is
    tracked here. The inner ``RLock`` does double duty: it makes nesting free
    within a thread, and it keeps two threads of one process from both
    reaching the file lock, whose own thread-local bookkeeping does not
    serialize them.
    """

    def __init__(self, lock_path: Path):
        """Guard the given ``.lock`` path."""
        self._thread_lock = threading.RLock()
        self._file_lock = FileLock(str(lock_path))
        self._depth = 0

    def __enter__(self) -> _ReentrantFileLock:
        """Acquire, taking the file lock only on the outermost entry."""
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                self._file_lock.acquire()
        except BaseException:
            self._thread_lock.release()
            raise
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        """Release, dropping the file lock only on the outermost exit."""
        self._depth -= 1
        try:
            if self._depth == 0:
                self._file_lock.release()
        finally:
            self._thread_lock.release()


class FileSessionStorage(SessionStorage):
    """JSONL file storage: one line per entry, one file per session.

    The format the manager has always written. Cross-process safety comes from
    a sibling ``.lock`` file, so two tau processes on the same session file
    serialize instead of interleaving.
    """

    def __init__(self, session_file: Path):
        """Initialize storage backed by the given ``.jsonl`` path."""
        self.session_file = Path(session_file)
        self.lock_path = Path(str(self.session_file) + ".lock")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _ReentrantFileLock(self.lock_path)

    @property
    def location(self) -> Path | None:
        """The session file path."""
        return self.session_file

    def exists(self) -> bool:
        """Whether the file holds anything.

        An existing but empty file is *not* durable state: ``rewrite([])``
        leaves the file in place, and reporting it as existing would diverge
        from the other backends, which have nothing left to report.
        """
        return self.session_file.exists() and self.session_file.stat().st_size > 0

    def lock(self) -> SessionLock:
        """Acquire the per-session file lock, reentrantly."""
        return self._lock

    def read(self) -> list[SessionFileEntry]:
        """Parse every line of the session file."""
        return read_session_file(self.session_file)

    def read_shedding(self) -> tuple[list[SessionFileEntry], set[str]]:
        """Read while skipping the bodies of compaction-folded messages.

        Safe here because the file on disk keeps the full content; the manager
        rehydrates it on demand through :meth:`read_entries_by_id`.
        """
        if not self.session_file.exists():
            return [], set()
        return read_session_file_shedding(self.session_file)

    def read_entries_by_id(self, ids: set[str]) -> dict[str, SessionFileEntry]:
        """Cheap-parse the file and fully validate only the requested ids."""
        if not ids or not self.session_file.exists():
            return {}

        return read_entries_by_id(self.session_file, ids)

    def append(self, entry: SessionEntry) -> None:
        """Append one JSON line under the session lock.

        Re-opens the path fresh rather than holding a handle, so a rewrite
        that replaced the inode (``os.replace``) is picked up instead of the
        append landing in an unlinked file.
        """
        with self.lock(), self.session_file.open("a", encoding="utf-8") as stream:
            stream.write(serialize_entry(entry) + "\n")

    def rewrite(self, entries: Sequence[SessionFileEntry]) -> None:
        """Replace the file atomically via a temp sibling and ``os.replace``."""
        content = "\n".join(serialize_entry(entry) for entry in entries)
        with self.lock():
            atomic_write_text(self.session_file, f"{content}\n" if content else "")


class InMemorySessionStorage(SessionStorage):
    """Non-persisting storage: the process holds the only copy.

    Backs untrusted-project sessions (which must not touch disk until the user
    grants trust) and tests. Because nothing is recoverable from elsewhere,
    :meth:`read_shedding` keeps the base class's shed-nothing behaviour —
    dropping message bodies here would destroy them.
    """

    def __init__(self, entries: Sequence[SessionFileEntry] | None = None):
        """Initialize empty, or seeded with the given entries."""
        self._entries: list[SessionFileEntry] = list(entries or [])
        self._lock = threading.RLock()

    @property
    def location(self) -> Path | None:
        """Always None: nothing is on disk."""
        return None

    def exists(self) -> bool:
        """Whether anything has been stored yet."""
        return bool(self._entries)

    def lock(self) -> SessionLock:
        """Acquire the in-process reentrant lock."""
        return self._lock

    def read(self) -> list[SessionFileEntry]:
        """Return a shallow copy so callers cannot mutate stored history.

        Applies the same header rule as the durable backends: history that
        does not begin with a session header is not a session.
        """
        with self._lock:
            if not self._entries or self._entries[0].type != SessionType.SESSION_HEADER:
                return []
            return list(self._entries)

    def append(self, entry: SessionEntry) -> None:
        """Append one entry."""
        with self._lock:
            self._entries.append(entry)

    def rewrite(self, entries: Sequence[SessionFileEntry]) -> None:
        """Replace all entries."""
        with self._lock:
            self._entries = list(entries)


class SQLiteSessionStorage(SessionStorage):
    """SQLite storage: one database per project, holding all its sessions.

    Tau already keeps one directory per project; this collapses that directory
    into a single file whose rows are scoped by ``session_id``. Projects stay
    independent — no cross-project write contention, and dropping a project's
    history is still one unlink.

    The reason for project scope rather than a file per session is listing.
    ``build_session_info`` derives a session's name and message count by
    reading its history, so ``/resume`` parses every line of every session
    file (measured: 0.24s for 35 MiB, growing with total bytes). Here those
    are one indexed query over :func:`list_sqlite_sessions`, with no payload
    parsing at all.

    ``seq`` is an autoincrementing surrogate that preserves append order
    within each session; the header is simply that session's first row, so
    :meth:`read` returns exactly the shape the file backend does. Entry ids
    are unique per session rather than globally, because a fork legitimately
    copies an entry id into a second session.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS entries (
        seq        INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        id         TEXT NOT NULL,
        type       TEXT NOT NULL,
        payload    TEXT NOT NULL,
        UNIQUE (session_id, id)
    );
    CREATE INDEX IF NOT EXISTS entries_session_seq ON entries (session_id, seq);
    CREATE INDEX IF NOT EXISTS entries_session_type ON entries (session_id, type);
    """

    def __init__(self, database: Path, session_id: str):
        """Initialize storage for one session inside the project database."""
        self.database = Path(database)
        self.session_id = session_id
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._depth = 0

    @property
    def location(self) -> Path | None:
        """The database file path."""
        return self.database

    def exists(self) -> bool:
        """Whether this session holds at least one entry in the database."""
        if not self.database.exists():
            return False
        cursor = self._connect().execute(
            "SELECT 1 FROM entries WHERE session_id = ? LIMIT 1", (self.session_id,)
        )
        return cursor.fetchone() is not None

    def _connect(self) -> sqlite3.Connection:
        """Open (once) the connection for this project's database."""
        if self._connection is None:
            self._connection = connect_sqlite(self.database)
        return self._connection

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Run the body inside one immediate transaction, reentrantly.

        Only the outermost acquisition begins and commits: nesting BEGIN is an
        error in SQLite, and the manager's read-merge-rewrite nests by design.
        BEGIN IMMEDIATE takes the write lock up front so two processes cannot
        both read, then both try to upgrade, and deadlock.
        """
        with self._lock:
            connection = self._connect()
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return

            connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
            finally:
                self._depth = 0

    def lock(self) -> SessionLock:
        """Acquire exclusive access as one reentrant SQLite transaction."""
        return self._transaction()

    def read(self) -> list[SessionFileEntry]:
        """Load this session's rows in append order.

        Mirrors :func:`~tau.session.utils.read_session_file`: an unparseable
        payload is skipped, and history that does not start with a session
        header reads as empty. A read never creates the database — probing a
        project that has none must not leave an empty one behind, which is
        what the file backend does and what listing code relies on.
        """
        if not self.database.exists():
            return []
        rows = self._connect().execute(
            "SELECT payload FROM entries WHERE session_id = ? ORDER BY seq",
            (self.session_id,),
        )
        entries: list[SessionFileEntry] = []
        for (payload,) in rows:
            try:
                entries.append(deserialize_entry(payload))
            except Exception:
                _log.warning(
                    "skipping unparseable entry in session %s of %s",
                    self.session_id,
                    self.database,
                )
                continue
        if not entries or entries[0].type != SessionType.SESSION_HEADER:
            return []
        return entries

    def append(self, entry: SessionEntry) -> None:
        """Insert one entry at the end of this session's history.

        A repeated id is ignored rather than duplicated: ids are unique within
        a session, and the manager already treats a re-seen id as the same
        durable entry when it merges (`_merged_durable_entries` keeps the
        first).
        """
        with self._transaction():
            self._connect().execute(
                "INSERT OR IGNORE INTO entries (session_id, id, type, payload) VALUES (?, ?, ?, ?)",
                (self.session_id, entry.id, str(entry.type), serialize_entry(entry)),
            )

    def rewrite(self, entries: Sequence[SessionFileEntry]) -> None:
        """Replace this session's rows inside one transaction.

        Atomic in the same sense as the file backend's temp-file swap: a crash
        mid-rewrite rolls back to the previous history rather than leaving it
        half-replaced. Other sessions in the database are untouched, so the
        surrogate key is not reset — it is shared with them.
        """
        with self._transaction():
            connection = self._connect()
            connection.execute("DELETE FROM entries WHERE session_id = ?", (self.session_id,))
            connection.executemany(
                "INSERT INTO entries (session_id, id, type, payload) VALUES (?, ?, ?, ?)",
                [
                    (self.session_id, entry.id, str(entry.type), serialize_entry(entry))
                    for entry in entries
                ],
            )

    def read_entries_by_id(self, ids: set[str]) -> dict[str, SessionFileEntry]:
        """Fetch only the requested ids, using the ``(session_id, id)`` index."""
        if not ids or not self.database.exists():
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self._connect().execute(
            f"SELECT id, payload FROM entries WHERE session_id = ? AND id IN ({placeholders})",
            (self.session_id, *ids),
        )
        found: dict[str, SessionFileEntry] = {}
        for entry_id, payload in rows:
            try:
                found[entry_id] = deserialize_entry(payload)
            except Exception:
                _log.warning("skipping unparseable entry %s in %s", entry_id, self.database)
        return found

    def close(self) -> None:
        """Close the underlying connection, if one was opened."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None


#: How long a blocked writer waits for the lock before giving up. Matters in a
#: way it did not when each session had its own file: several sessions of one
#: project now write to the same database and must queue, not fail.
_SQLITE_TIMEOUT_SECONDS = 30.0

#: Attempts to win the brief exclusive lock that switching journal mode needs.
_WAL_ATTEMPTS = 20
_WAL_RETRY_DELAY_SECONDS = 0.01


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Put the database in WAL mode, tolerating a concurrent enabler.

    Switching journal mode needs a brief exclusive lock, and SQLite does *not*
    run the busy handler for it — so a connect storm on a fresh database
    raises "database is locked" immediately, however long the busy timeout is.
    Losing that race is harmless: the journal mode is a persistent property of
    the file, so whoever won has already set it for everyone. Correctness never
    depends on WAL; only read/write concurrency does, which is why failing all
    the attempts falls through to the file's existing mode instead of raising.
    """
    if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
        return
    for attempt in range(_WAL_ATTEMPTS):
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if attempt == _WAL_ATTEMPTS - 1:
                _log.debug("could not switch %s to WAL; using its current journal mode", connection)
                return
            time.sleep(_WAL_RETRY_DELAY_SECONDS)


def connect_sqlite(database: Path) -> sqlite3.Connection:
    """Open a project session database, creating it and its schema if needed."""
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: transactions are driven explicitly by lock(), so
    # sqlite3's implicit BEGIN must stay out of the way.
    connection = sqlite3.connect(
        database,
        isolation_level=None,
        check_same_thread=False,
        timeout=_SQLITE_TIMEOUT_SECONDS,
    )
    # Set before any other statement so the schema DDL below is covered too.
    connection.execute(f"PRAGMA busy_timeout = {int(_SQLITE_TIMEOUT_SECONDS * 1000)}")
    # WAL lets a reader proceed while a writer holds the write lock, which
    # matches the file backend's behaviour of never blocking reads.
    _enable_wal(connection)
    connection.executescript(SQLiteSessionStorage._SCHEMA)
    return connection


def list_sqlite_sessions(database: Path) -> list[SessionInfo]:
    """List every session in a project database without parsing its history.

    This is the point of the per-project layout. The JSONL equivalent,
    :func:`~tau.session.utils.build_session_info`, derives ``message_count``
    by reading every line of every session file, so listing costs O(total
    bytes on disk). Here the count is an indexed ``GROUP BY`` and only the
    header and name rows are ever deserialized.
    """
    database = Path(database)
    if not database.exists():
        return []

    connection = connect_sqlite(database)
    try:
        counts = {
            session_id: count
            for session_id, count in connection.execute(
                "SELECT session_id, COUNT(*) FROM entries WHERE type = ? GROUP BY session_id",
                (str(SessionType.SESSION_MESSAGE),),
            )
        }
        # The newest name wins, matching the manager's latest-write-wins read.
        names = {
            session_id: payload
            for session_id, payload in connection.execute(
                "SELECT session_id, payload FROM entries WHERE type = ? ORDER BY seq",
                (str(SessionType.SESSION_INFO),),
            )
        }
        headers = connection.execute(
            "SELECT session_id, payload FROM entries WHERE type = ? ORDER BY seq",
            (str(SessionType.SESSION_HEADER),),
        ).fetchall()
    finally:
        connection.close()

    modified = datetime.fromtimestamp(database.stat().st_mtime)
    infos: list[SessionInfo] = []
    for session_id, payload in headers:
        try:
            header = deserialize_entry(payload)
        except Exception:
            _log.warning("skipping unparseable header for session %s in %s", session_id, database)
            continue
        if not isinstance(header, SessionHeader):
            continue

        name: str | None = None
        if (name_payload := names.get(session_id)) is not None:
            with suppress(Exception):
                name_entry = deserialize_entry(name_payload)
                name = getattr(name_entry, "name", None)

        infos.append(
            SessionInfo(
                path=database,
                id=header.id,
                cwd=header.cwd,
                name=name,
                parent_session=header.parent_session,
                created=datetime.fromtimestamp(header.timestamp),
                modified=modified,
                message_count=counts.get(session_id, 0),
            )
        )
    return infos
