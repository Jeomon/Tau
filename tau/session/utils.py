import contextlib
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

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
    """Generate a Unix timestamp for the current moment."""
    now = datetime.now()
    return now.timestamp()


def get_default_project_session_dir(cwd: str | Path, sessions_dir: Path | None = None) -> Path:
    """Return the per-project session directory under ~/.tau/sessions/<encoded-cwd>/."""
    base = sessions_dir if sessions_dir is not None else get_sessions_dir()
    resolved = str(Path(cwd).resolve())
    # Encode the absolute path into a safe directory name: --home-user-project--
    safe = (
        "--"
        + re.sub(r"^[/\\]", "", resolved).replace("/", "-").replace("\\", "-").replace(":", "-")
        + "--"
    )
    session_dir = base / safe
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# Backward-compatible alias for integrations using the pre-0.4.5 public name.
get_default_session_dir = get_default_project_session_dir


def read_session_file(session_file: Path) -> list[SessionFileEntry]:
    """Load and parse a session file, returning a list of entries."""
    if not session_file.exists():
        return []

    adapter = TypeAdapter(SessionFileEntry)

    content = session_file.read_text(encoding="utf-8")
    entries: list[SessionFileEntry] = []

    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = adapter.validate_json(line)
            entries.append(entry)
        except Exception:
            continue

    if len(entries) == 0:
        return []

    header = entries[0]

    if header.type != SessionType.SESSION_HEADER:
        return []

    return entries


def is_valid_session_file(session_file: Path | str) -> bool:
    """Check if a file is a valid session file by validating its header."""
    try:
        path = Path(session_file)
        if not path.exists():
            return False

        with path.open("r", encoding="utf-8") as file:
            first_line = file.readline().strip()

        if not first_line:
            return False

        SessionHeader.model_validate_json(first_line)
        return True
    except (OSError, ValidationError, ValueError):
        return False


def find_most_recent_session(session_dir: Path | str) -> Path | None:
    """Find the most recently modified session file in a directory."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return None

    candidate_sessions = [p for p in session_dir.glob("*.jsonl") if is_valid_session_file(p)]

    if not candidate_sessions:
        return None

    most_recent = max(candidate_sessions, key=lambda x: x.stat().st_mtime)
    return most_recent


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
    """Parse a session file and extract metadata into a SessionInfo object."""
    content = file.read_text(encoding="utf-8")

    file_entries: list[SessionFileEntry] = []
    lines = content.strip().splitlines()
    adapter = TypeAdapter(SessionFileEntry)

    for line in lines:
        if not line.strip():
            continue
        with contextlib.suppress(Exception):
            file_entries.append(adapter.validate_json(line))

    if len(file_entries) == 0:
        return None

    header: SessionHeader | None = None
    entries: list[SessionEntry] = []
    message_count = 0
    for entry in file_entries:
        if isinstance(entry, SessionHeader):
            header = entry
        else:
            entries.append(entry)
            if isinstance(entry, MessageEntry):
                message_count += 1

    if header is None:
        return None

    cwd = header.cwd
    parent_session = header.parent_session
    created = datetime.fromtimestamp(header.timestamp)
    modified = get_session_modified_date(entries, header)

    return SessionInfo(
        path=file,
        id=header.id,
        cwd=cwd,
        parent_session=parent_session,
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
