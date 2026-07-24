import contextlib
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pydantic_core
from pydantic import TypeAdapter, ValidationError
from uuid_extensions import uuid7str as _uuid7str

from tau.message.types import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    LLMMessage,
    Role,
    TerminalExecutionMessage,
    TextContent,
    ToolMessage,
    UserMessage,
)
from tau.session.types import (
    MessageEntry,
    SessionEntry,
    SessionFileEntry,
    SessionHeader,
    SessionInfo,
    SessionType,
)
from tau.settings.paths import get_sessions_dir

_log = logging.getLogger(__name__)

# Built once at import time rather than per-call: constructing a TypeAdapter
# for a discriminated union costs ~8ms (schema build), which used to be paid
# on every read_session_file() call and again per file in build_session_info()
# — the latter runs once per session when listing sessions, so on a project
# with many saved sessions that added up to real, avoidable startup latency.
_SESSION_FILE_ENTRY_ADAPTER: TypeAdapter[SessionFileEntry] = TypeAdapter(SessionFileEntry)
_SESSION_HEADER_ADAPTER: TypeAdapter[SessionHeader] = TypeAdapter(SessionHeader)


def create_session_id() -> str:
    """Create a new session ID using UUIDv7."""
    return _uuid7str()


def generate_id(by_id: Any) -> str:
    """
    Generate a unique short ID (8 hex chars, collision-checked).

    Args:
        by_id: A container (like a set or dict) that supports the 'in' operator
               to check for existing IDs.
    """
    for _ in range(100):
        new_id = str(uuid.uuid4())[:8]
        if new_id not in by_id:
            return new_id

    # Fallback to full UUID if somehow we have collisions
    return str(uuid.uuid4())


def generate_timestamp() -> float:
    """Generate a Unix timestamp for the current moment.

    Reads the epoch clock directly rather than going through naive
    ``datetime.now()``, whose ``.timestamp()`` re-interprets the value as local
    time — ambiguous during a DST fall-back (an hour that occurs twice), and
    subject to µs→float rounding that could place the result just outside the
    interval a caller measured around it.
    """
    return time.time()


def get_default_project_session_dir(cwd: str | Path, sessions_dir: Path | None = None) -> Path:
    """Return the per-project session directory under ~/.tau/sessions/<encoded-cwd>/."""
    base = sessions_dir if sessions_dir is not None else get_sessions_dir()
    resolved = str(Path(cwd).resolve())
    # Encode the absolute path into a safe directory name: --home-user-project--
    stem = "--" + re.sub(r"^[/\\]", "", resolved).replace("/", "-").replace("\\", "-").replace(
        ":", "-"
    )
    # The separator-flattening encoding is not injective (/x/my-app and /x/my/app
    # collide), so new directories append a short hash of the raw path. Legacy
    # directories (no hash) stay findable so existing sessions keep working.
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]
    session_dir = base / f"{stem}-{digest}--"
    legacy_dir = base / f"{stem}--"
    if not session_dir.exists() and legacy_dir.exists():
        return legacy_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# Backward-compatible alias for integrations using the pre-0.4.5 public name.
get_default_session_dir = get_default_project_session_dir


def read_session_file(session_file: Path) -> list[SessionFileEntry]:
    """Load and parse a session file, returning a list of entries."""
    if not session_file.exists():
        return []

    content = session_file.read_text(encoding="utf-8")
    entries: list[SessionFileEntry] = []

    for lineno, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = _SESSION_FILE_ENTRY_ADAPTER.validate_json(line)
            entries.append(entry)
        except Exception:
            _log.warning("skipping unparseable line %d in session file %s", lineno, session_file)
            continue

    if len(entries) == 0:
        return []

    header = entries[0]

    if header.type != SessionType.SESSION_HEADER:
        return []

    return entries


def read_session_file_shedding(session_file: Path) -> tuple[list[SessionFileEntry], set[str]]:
    """Load a session file, skipping full validation of message content that
    ``SessionManager`` would immediately shed anyway.

    ``SessionManager`` frees the heavy body (tool results, file contents,
    images) of every ``MessageEntry`` before the most recent compaction's
    kept window right after loading (see ``_shed_folded_message_content``) --
    the full content stays authoritative on disk and is only re-read if
    something old is navigated back into. For a long, already-compacted
    session that means ``read_session_file`` pays full pydantic validation
    (the dominant cost of a resume) for content that is thrown away a few
    lines later.

    This does the same lightweight ``json.loads`` pass ``read_session_file``
    implicitly does inside pydantic's JSON parser, but as a plain Python pass
    first so the branch (and its most recent compaction boundary) can be
    found cheaply, *before* the expensive step. Entries before that boundary
    have their ``message.contents`` cleared in the raw dict before
    validation, skipping pydantic construction/validation of that nested
    content entirely. Every other entry (kept window, non-message entries,
    entries off the current branch) is validated in full, byte-for-byte the
    same as ``read_session_file`` would produce.

    Returns ``(entries, shed_ids)`` -- ``shed_ids`` are the ids whose content
    was stripped, for the caller to fold directly into
    ``SessionManager._shed_ids`` instead of re-deriving them with a second
    branch walk.

    Falls back to ``read_session_file`` (with an empty shed set) for any
    shape it doesn't specifically optimize -- missing/invalid header, no
    compaction on the branch, etc. -- so behaviour for those stays governed
    by the one well-exercised implementation.
    """
    if not session_file.exists():
        return [], set()

    content = session_file.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return [], set()

    raw: list[dict[str, Any] | None] = []
    for line in lines:
        try:
            # pydantic-core's Rust JSON parser -- ~2-3x faster here than the
            # stdlib json module for this shape (many/large string values),
            # and it's already a transitive dependency of every entry model
            # below, not a new one.
            obj = pydantic_core.from_json(line)
        except Exception:
            obj = None
        raw.append(obj if isinstance(obj, dict) else None)

    header_obj = raw[0]
    if header_obj is None or header_obj.get("type") != SessionType.SESSION_HEADER:
        return read_session_file(session_file), set()

    # Fast path: without any tree navigation (no LeafEntry redirecting the
    # leaf elsewhere), append order already *is* root->leaf order -- branch()
    # is the only thing that can make the two diverge (branch_with_summary()
    # also reassigns leaf_id but always immediately appends a BranchSummaryEntry
    # right where the jump happened, which the parent-id check below still
    # catches). That's the common case (compacted but never /tree-navigated),
    # so two flat forward scans replace building a by_id map and walking
    # parent pointers backward through it.
    is_linear = True
    prev_id: str | None = None
    for obj in raw[1:]:
        if obj is None:
            continue
        if obj.get("type") == SessionType.LEAF or obj.get("parent_id") != prev_id:
            is_linear = False
            break
        prev_id = obj.get("id")

    first_kept_id: str | None = None
    shed_ids: set[str] = set()

    if is_linear:
        seen_ids: set[str] = set()
        for obj in raw[1:]:
            if obj is None:
                continue
            entry_id = obj.get("id")
            if isinstance(entry_id, str):
                seen_ids.add(entry_id)
            if obj.get("type") == SessionType.COMPACTION:
                first_kept_id = obj.get("first_kept_entry_id")

        if first_kept_id is not None and first_kept_id in seen_ids:
            for obj in raw[1:]:
                if obj is None:
                    continue
                entry_id = obj.get("id")
                if entry_id == first_kept_id:
                    break
                if obj.get("type") != SessionType.SESSION_MESSAGE:
                    continue
                message = obj.get("message")
                if not isinstance(message, dict) or message.get("role") not in (
                    "user",
                    "assistant",
                    "tool",
                ):
                    continue
                if message.get("contents"):
                    message["contents"] = []
                    if isinstance(entry_id, str):
                        shed_ids.add(entry_id)
    else:
        # General path: entries may not all sit on the current leaf's branch
        # (tree navigation happened at some point) -- resolve the actual
        # branch via parent pointers, exactly like SessionManager.get_branch(),
        # before deciding what to shed.
        by_id: dict[str, dict[str, Any]] = {}
        leaf_id: str | None = None
        for obj in raw[1:]:
            if obj is None or not isinstance(obj.get("id"), str):
                continue
            entry_id = obj["id"]
            by_id[entry_id] = obj
            leaf_id = obj.get("target_id") if obj.get("type") == SessionType.LEAF else entry_id

        branch_ids: list[str] = []
        cursor = leaf_id
        visited: set[str] = set()
        while cursor and cursor in by_id and cursor not in visited:
            visited.add(cursor)
            branch_ids.append(cursor)
            cursor = by_id[cursor].get("parent_id")
        branch_ids.reverse()

        for entry_id in branch_ids:
            if by_id[entry_id].get("type") == SessionType.COMPACTION:
                first_kept_id = by_id[entry_id].get("first_kept_entry_id")

        if first_kept_id is not None and first_kept_id in by_id:
            for entry_id in branch_ids:
                if entry_id == first_kept_id:
                    break
                obj = by_id[entry_id]
                if obj.get("type") != SessionType.SESSION_MESSAGE:
                    continue
                message = obj.get("message")
                if not isinstance(message, dict) or message.get("role") not in (
                    "user",
                    "assistant",
                    "tool",
                ):
                    continue
                if message.get("contents"):
                    message["contents"] = []
                    shed_ids.add(entry_id)

    entries: list[SessionFileEntry] = []
    for lineno, obj in enumerate(raw, start=1):
        if obj is None:
            continue
        try:
            entries.append(_SESSION_FILE_ENTRY_ADAPTER.validate_python(obj))
        except Exception:
            _log.warning("skipping unparseable line %d in session file %s", lineno, session_file)
            continue

    if len(entries) == 0 or entries[0].type != SessionType.SESSION_HEADER:
        return [], set()

    return entries, shed_ids


def count_session_data_lines(session_file: Path) -> int:
    """Count non-blank lines in a session file, parseable or not.

    Used to detect unparseable lines (compare against ``len(read_session_file())``)
    so a rewrite can preserve the original file instead of silently dropping them.
    """
    try:
        content = session_file.read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in content.splitlines() if line.strip())


def read_session_header(session_file: Path | str) -> SessionHeader | None:
    """Read and validate only the first line (header) of a session file."""
    try:
        path = Path(session_file)
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            first_line = file.readline().strip()

        if not first_line:
            return None

        return SessionHeader.model_validate_json(first_line)
    except (OSError, ValidationError, ValueError):
        return None


def is_valid_session_file(session_file: Path | str) -> bool:
    """Check if a file is a valid session file by validating its header."""
    return read_session_header(session_file) is not None


def find_most_recent_session(session_dir: Path | str, cwd: Path | str | None = None) -> Path | None:
    """Find the most recently modified session file in a directory.

    Ranks files by mtime first (a cheap ``stat()``, no file open) and only
    validates them newest-first until one passes — a project accumulates
    session files forever with nothing to prune them, so validating every
    file up front (opening and parsing each one) scaled with total lifetime
    session count instead of stopping at the first valid candidate.

    When *cwd* is given, sessions whose header records a different working
    directory are skipped — the legacy directory-name encoding is not
    injective (``/x/my-app`` vs ``/x/my/app``), so a directory can contain
    another project's sessions.
    """
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return None
    expected_cwd = Path(cwd).resolve() if cwd is not None else None

    def _mtime_or_min(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return float("-inf")

    by_mtime = sorted(session_dir.glob("*.jsonl"), key=_mtime_or_min, reverse=True)

    for candidate in by_mtime:
        header = read_session_header(candidate)
        if header is None:
            continue
        if expected_cwd is not None and Path(header.cwd).resolve() != expected_cwd:
            continue
        return candidate
    return None


def is_message_with_contents(message: AgentMessage) -> bool:
    """Check if a message is an LLM message with user or assistant role and content."""
    if not isinstance(message, LLMMessage):
        return False
    if message.role not in (Role.USER, Role.ASSISTANT):
        return False
    return any(isinstance(c, (TextContent, ImageContent)) for c in message.contents)


def get_last_activity_time(entries: list[SessionEntry]) -> float | None:
    """Extract the most recent message timestamp from a list of session entries."""
    last_activity_time = None

    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue

        if not is_message_with_contents(entry.message):
            continue

        message_timestamp = getattr(entry.message, "timestamp", None)
        if message_timestamp is None:
            timestamp = entry.timestamp
        elif isinstance(message_timestamp, (int, float)):
            timestamp = float(message_timestamp)
        else:
            timestamp = float(message_timestamp.timestamp())

        last_activity_time = max(last_activity_time or 0.0, timestamp)

    return last_activity_time


def get_session_modified_date(
    entries: list[SessionEntry], header: SessionHeader | None = None
) -> datetime:
    """Get the modified timestamp of a session, based on last activity or header creation time."""
    if last_activity_time := get_last_activity_time(entries=entries):
        return datetime.fromtimestamp(last_activity_time)

    if header is None:
        # Try to find a header in the entries
        for entry in entries:
            if isinstance(entry, SessionHeader):
                header = entry
                break
    if header is None:
        # Fallback to current time if no header found
        return datetime.now()
    return datetime.fromtimestamp(header.timestamp)


def build_session_info(file: Path) -> SessionInfo | None:
    """Parse a session file and extract metadata into a SessionInfo object.

    Fast path: only the first line (header) and first ~30 lines (for the name)
    are read; ``modified`` comes from the filesystem mtime so we never deserialize
    the whole conversation history just to list sessions.
    """

    try:
        stat = file.stat()
    except OSError:
        return None

    header: SessionHeader | None = None
    name: str | None = None
    message_count = 0

    try:
        with file.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                with contextlib.suppress(Exception):
                    obj = json.loads(raw)
                    entry_type = obj.get("type")
                    if entry_type == SessionType.SESSION_HEADER and header is None:
                        header = _SESSION_HEADER_ADAPTER.validate_python(obj)
                    elif entry_type == SessionType.SESSION_INFO and name is None:
                        name = obj.get("name")
                    elif entry_type == SessionType.SESSION_MESSAGE:
                        message_count += 1
    except OSError:
        return None

    if header is None:
        return None

    modified = datetime.fromtimestamp(stat.st_mtime)
    created = datetime.fromtimestamp(header.timestamp)

    return SessionInfo(
        path=file,
        id=header.id,
        cwd=header.cwd,
        name=name,
        parent_session=header.parent_session,
        created=created,
        modified=modified,
        message_count=message_count,
    )


def list_sessions_from_dir(
    dir_path: Path | str,
    on_progress: Callable[[int, int], None] | None = None,
    progress_offset: int = 0,
    progress_total: int | None = None,
) -> list[SessionInfo]:
    """
    Read all .jsonl session files in a directory and return a list of SessionInfo objects.
    Optionally reports progress through the on_progress callback.
    """
    sessions: list[SessionInfo] = []
    dir_path = Path(dir_path)

    if not dir_path.exists() or not dir_path.is_dir():
        return sessions

    try:
        files = list(dir_path.glob("*.jsonl"))
        total = progress_total if progress_total is not None else len(files)
        # We process files sequentially since Python I/O blocking is usually fine here,
        # but could be updated to use ThreadPoolExecutor if concurrency is strictly needed.
        for loaded, file in enumerate(files, 1):
            info = build_session_info(file)
            if on_progress:
                on_progress(progress_offset + loaded, total)

            if info is not None:
                sessions.append(info)

    except Exception:
        _log.warning("failed to list sessions from %s", dir_path, exc_info=True)

    return sessions


@dataclass
class SessionPager:
    """Incrementally parse session files in newest-first filesystem order."""

    _files: list[Path]
    _cursor: int = 0

    @property
    def total_count(self) -> int:
        """Return the cheap filesystem-discovery count before JSONL parsing."""
        return len(self._files)

    @classmethod
    def from_directory(cls, directory: Path | str) -> "SessionPager":
        return cls(_session_files_from_dirs([Path(directory)]))

    @classmethod
    def from_all_directories(cls) -> "SessionPager":
        sessions_dir = get_sessions_dir()
        try:
            directories = [path for path in sessions_dir.iterdir() if path.is_dir()]
        except OSError:
            directories = []
        return cls(_session_files_from_dirs(directories))

    def next_page(self, page_size: int) -> tuple[list[SessionInfo], bool]:
        """Parse up to ``page_size`` valid sessions and report whether more remain."""
        sessions: list[SessionInfo] = []
        while self._cursor < len(self._files) and len(sessions) < page_size:
            file = self._files[self._cursor]
            self._cursor += 1
            if info := build_session_info(file):
                sessions.append(info)
        return sessions, self._cursor < len(self._files)


def _session_files_from_dirs(directories: list[Path]) -> list[Path]:
    """Discover session files cheaply, ordered by filesystem modification time."""
    files: list[tuple[float, Path]] = []
    for directory in directories:
        try:
            for file in directory.glob("*.jsonl"):
                try:
                    files.append((file.stat().st_mtime, file))
                except OSError:
                    continue
        except OSError:
            continue
    files.sort(key=lambda item: item[0], reverse=True)
    return [file for _mtime, file in files]


def to_llm_messages(messages: list[AgentMessage]) -> list[LLMMessage]:
    """Convert AgentMessages to LLM-compatible messages.

    TerminalExecutionMessage   → UserMessage (Ran `cmd`\n```output```)
    CompactionSummaryMessage → UserMessage with summary wrapped in XML tags
    CustomMessage and other non-LLM types → skipped
    Empty AssistantMessages are visual-only markers (aborts, persisted API/credit
    errors) and are skipped — an assistant turn with neither content nor tool
    calls is invalid to send back and triggers provider 400s.
    """
    from tau.message.types import CompactionSummaryMessage, ThinkingContent, ToolCallContent

    result: list[LLMMessage] = []
    for msg in messages:
        if isinstance(msg, CompactionSummaryMessage):
            text = f"<context-summary>\n{msg.summary}\n</context-summary>"
            result.append(UserMessage.from_text(text))
        elif isinstance(msg, TerminalExecutionMessage):
            if not msg.exclude:
                result.append(msg.to_user_message())
        elif isinstance(msg, AssistantMessage):
            has_usable = any(
                isinstance(c, (TextContent, ToolCallContent, ThinkingContent)) for c in msg.contents
            )
            if has_usable:
                result.append(msg)
        elif isinstance(msg, (UserMessage, ToolMessage)):
            result.append(msg)
    return result
