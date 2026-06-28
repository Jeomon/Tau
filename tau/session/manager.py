from __future__ import annotations

import builtins
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from tau.inference.types import ThinkingLevel
from tau.message.types import (
    AgentMessage,
    AssistantMessage,
    CustomMessage,
)
from tau.session.types import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomInfoEntry,
    CustomMessageEntry,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    MessageMeta,
    ModelChangeEntry,
    SessionContext,
    SessionEntry,
    SessionFileEntry,
    SessionHeader,
    SessionInfo,
    SessionInfoEntry,
    SessionOptions,
    SessionTreeNode,
    ThinkingLevelChangeEntry,
)
from tau.session.utils import (
    create_session_id,
    find_most_recent_session,
    generate_id,
    generate_timestamp,
    get_default_project_session_dir,
    list_sessions_from_dir,
    read_session_file,
)
from tau.settings.paths import get_sessions_dir

_log = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        cwd: str | Path,
        session_dir: Path | None = None,
        session_file: Path | None = None,
        persist: bool = True,
    ):
        self.session_id: str | None = None
        self.cwd = Path(cwd).resolve()
        self.persist = persist
        self.session_dir = (
            Path(session_dir).resolve() if session_dir else get_default_project_session_dir(self.cwd)
        )
        self.session_file = session_file
        self.by_id: dict[str, SessionEntry] = {}
        self.labels_by_id: dict[str, str] = {}
        self.label_timestamps_by_id: dict[str, float] = {}
        self.leaf_id: str | None = None
        self.entries: list[SessionFileEntry] = []
        self.flushed: bool = False

        if self.persist and not self.session_dir.exists():
            self.session_dir.mkdir(parents=True, exist_ok=True)

        if self.session_file:
            self.set_session(self.session_file)
        else:
            self.new_session()

    def enable_persist(self) -> None:
        """Switch from a non-persisting session to a persisting one.

        Called after the user grants project trust. Creates the session
        directory and writes buffered entries to disk so nothing is lost.
        """
        if self.persist:
            return
        self.persist = True
        self.session_dir.mkdir(parents=True, exist_ok=True)
        # Materialise the session file path that new_session() skipped earlier.
        if self.session_file is None and self.session_id is not None:
            from datetime import datetime
            file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
            self.session_file = (
                self.session_dir / f"{file_timestamp}_{self.session_id}.jsonl"
            ).resolve()
        self._rewrite_file()

    def set_session(self, session_file: Path):
        """Load or initialize a session from a file."""
        self.session_file = session_file
        if session_file.exists():
            self.entries = read_session_file(session_file)

        if not self.entries:
            # File missing or invalid — start fresh in that file location
            session_id = create_session_id()
            header = SessionHeader(
                id=session_id,
                timestamp=generate_timestamp(),
                cwd=self.cwd,
            )
            self.session_id = session_id
            self.entries = [header]
            self._clear_index()
            self.flushed = False

            if self.persist:
                self._rewrite_file()
                self.flushed = True
        else:
            for entry in self.entries:
                if isinstance(entry, SessionHeader):
                    self.session_id = entry.id
                    break
            self._build_index()
            self.flushed = True

    def new_session(self, options: SessionOptions | None = None):
        """Create a new session, optionally with parent session and custom ID."""
        options = options or SessionOptions()
        session_id = options.id or create_session_id()
        parent_session = Path(options.parent_session).resolve() if options.parent_session else None
        header = SessionHeader(
            id=session_id,
            timestamp=generate_timestamp(),
            cwd=self.cwd,
            parent_session=parent_session,
        )

        self.session_id = session_id
        self.entries = [header]
        self._clear_index()
        self.flushed = False

        if self.persist:
            file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
            self.session_file = (
                self.session_dir / f"{file_timestamp}_{session_id}.jsonl"
            ).resolve()

        return self.session_file

    def _rewrite_file(self):
        """Write all session entries to the session file."""
        if not self.persist or not self.session_file:
            return None
        lines = [entry.model_dump_json(exclude_none=True) for entry in self.entries]
        try:
            self.session_file.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            _log.error("failed to rewrite session file %s", self.session_file, exc_info=True)

    def _clear_index(self):
        """Clear the session indices."""
        self.by_id.clear()
        self.labels_by_id.clear()
        self.label_timestamps_by_id.clear()
        self.leaf_id = None

    def _build_index(self):
        """Rebuild internal indices from loaded entries."""
        self.by_id.clear()
        self.labels_by_id.clear()
        self.label_timestamps_by_id.clear()
        self.leaf_id = None

        for entry in self.entries:
            if isinstance(entry, SessionHeader):
                continue
            self.by_id[entry.id] = entry
            
            if isinstance(entry, LeafEntry):
                # LeafEntry records a navigation point — target_id is the new leaf.
                self.leaf_id = entry.target_id
            else:
                self.leaf_id = entry.id

            if isinstance(entry, LabelEntry):
                if entry.label:
                    self.labels_by_id[entry.target_id] = entry.label
                    self.label_timestamps_by_id[entry.target_id] = entry.timestamp
                else:
                    self.labels_by_id.pop(entry.target_id, None)
                    self.label_timestamps_by_id.pop(entry.target_id, None)

    def _persist(self, entry: SessionEntry):
        """Append an entry to the session file."""
        if not self.persist or not self.session_file:
            return None

        has_assistant_message = any(
            isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
            for e in self.entries
        )

        if not has_assistant_message:
            self.flushed = False
            return

        try:
            import json

            with self.session_file.open("a", encoding="utf-8") as f:
                if not self.flushed:
                    for e in self.entries:
                        json.dump(e.model_dump(exclude_none=True), f, ensure_ascii=False)
                        f.write("\n")
                    self.flushed = True
                else:
                    json.dump(entry.model_dump(exclude_none=True), f, ensure_ascii=False)
                    f.write("\n")
        except OSError:
            _log.error("failed to persist session entry to %s", self.session_file, exc_info=True)

    def _append_entry(self, entry: SessionEntry) -> str:
        """Add an entry to the session and persist it."""
        self.entries.append(entry)
        self.by_id[entry.id] = entry
        self.leaf_id = entry.id
        self._persist(entry)
        return entry.id

    def append_message(self, message: AgentMessage, meta: MessageMeta | None = None) -> str:
        """Add a message to the session."""
        entry = MessageEntry(message=message, parent_id=self.leaf_id, meta=meta)
        return self._append_entry(entry)

    def remove_last_message(self, role: str | None = None) -> bool:
        """Remove the message entry at the current leaf, if it matches role.

        Only ever touches the entry at the tip of the *current* branch — never
        reaches into other branches — so this stays correct after navigating
        the tree. Returns True if an entry was removed.
        """
        entry = self.by_id.get(self.leaf_id) if self.leaf_id is not None else None
        if not isinstance(entry, MessageEntry):
            return False
        if role is not None and getattr(entry.message, "role", None) != role:
            return False
        self.entries.remove(entry)
        self.by_id.pop(entry.id, None)
        self.leaf_id = entry.parent_id
        if self.flushed:
            self._rewrite_file()
        return True

    def find_last_assistant_message(self) -> AssistantMessage | None:
        """Return the most recent AssistantMessage in the active branch, or None."""
        from tau.message.types import AssistantMessage

        for entry in reversed(self.get_branch()):
            if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
                return entry.message
        return None

    def append_thinking_level_change(self, thinking_level: ThinkingLevel) -> str:
        """Record a change in the thinking level setting."""
        entry = ThinkingLevelChangeEntry(thinking_level=thinking_level, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def append_model_change(self, model_id: str, provider_id: str) -> str:
        """Record a model or provider change."""
        entry = ModelChangeEntry(model_id=model_id, provider_id=provider_id, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def append_label_change(self, target_id: str, label: str | None = None) -> str:
        """Add or remove a label from an entry."""
        entry = LabelEntry(target_id=target_id, label=label, parent_id=self.leaf_id)
        if label:
            self.labels_by_id[target_id] = label
            self.label_timestamps_by_id[target_id] = entry.timestamp
        else:
            self.labels_by_id.pop(target_id, None)
            self.label_timestamps_by_id.pop(target_id, None)
        return self._append_entry(entry)

    def append_custom_info(self, custom_type: str, data: Any | None = None) -> str:
        """Add custom metadata to the session."""
        entry = CustomInfoEntry(custom_type=custom_type, data=data, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def append_custom_message(
        self,
        custom_type: str,
        content: Any,
        display: bool = True,
        details: Any | None = None,
    ) -> str:
        """Add a custom message to the session."""
        entry = CustomMessageEntry(
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
            parent_id=self.leaf_id,
        )
        return self._append_entry(entry)

    def append_branch_summary(
        self,
        from_id: str,
        summary: str,
        details: dict | None = None,
        from_hook: bool = False,
        label: str | None = None,
    ) -> str:
        """Record a summary when abandoning a branch."""
        entry = BranchSummaryEntry(
            from_id=from_id,
            summary=summary,
            details=details,
            from_hook=from_hook,
            label=label,
            parent_id=self.leaf_id,
        )
        return self._append_entry(entry)

    def branch_with_summary(
        self,
        branch_from_id: str | None,
        summary: str,
        details: dict | None = None,
        from_hook: bool = False,
    ) -> str:
        """Navigate to branch_from_id and append a branch_summary entry
        capturing the abandoned path.
        """
        if branch_from_id is not None and branch_from_id not in self.by_id:
            raise KeyError(f"Entry {branch_from_id} not found.")
        self.leaf_id = branch_from_id
        entry = BranchSummaryEntry(
            from_id=branch_from_id or "root",
            summary=summary,
            details=details,
            from_hook=from_hook,
            parent_id=branch_from_id,
        )
        return self._append_entry(entry)

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: dict | None = None,
    ) -> str:
        """Record a context compaction."""
        entry = CompactionEntry(
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            details=details,
            parent_id=self.leaf_id,
        )
        return self._append_entry(entry)

    def append_session_info(self, name: str) -> str:
        """Set the session name."""
        entry = SessionInfoEntry(name=name, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def get_session_name(self) -> str | None:
        """Return the most recent session name, or None if not set."""
        for entry in reversed(self.entries):
            if isinstance(entry, SessionInfoEntry) and entry.name and entry.name.strip():
                return entry.name.strip()
        return None

    def get_leaf_id(self) -> str | None:
        """Return the ID of the current leaf entry, or None if not set."""
        return self.leaf_id

    def get_leaf_entry(self) -> SessionEntry | None:
        """Return the current leaf entry, or None if not found."""
        return self.by_id.get(self.leaf_id) if self.leaf_id else None

    def get_entry(self, id: str) -> SessionEntry | None:
        """Retrieve an entry by ID, or None if not found."""
        return self.by_id.get(id)

    def get_children(self, parent_id: str) -> list[SessionEntry]:
        """Return all entries with the given parent_id, sorted by timestamp."""
        return sorted(
            [entry for entry in self.get_entries() if entry.parent_id == parent_id],
            key=lambda entry: entry.timestamp,
        )

    def get_label(self, id: str) -> str | None:
        """Return the label for an entry, or None if not labeled."""
        return self.labels_by_id.get(id)

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        """Return entries from root to the given id (or leaf_id), in root→leaf order."""
        path: list[SessionEntry] = []
        cursor = from_id or self.leaf_id
        while cursor:
            current_entry = self.by_id.get(cursor)
            if not current_entry:
                break
            path.append(current_entry)
            cursor = current_entry.parent_id
        path.reverse()
        return path

    def build_session_context(self) -> SessionContext:
        """Build a context object from the current branch, including messages and settings."""
        from tau.message.types import CompactionSummaryMessage

        thinking_level: ThinkingLevel = ThinkingLevel.Off
        model_id: str | None = None
        provider_id: str | None = None
        messages: list[AgentMessage] = []

        entries = self.get_branch()

        if not entries:
            return SessionContext(
                messages=messages,
                thinking_level=thinking_level,
                model_id=model_id,
                provider_id=provider_id,
            )

        # Scan all entries for model/thinking-level changes
        for entry in entries:
            match entry:
                case ThinkingLevelChangeEntry():
                    thinking_level = entry.thinking_level
                case ModelChangeEntry():
                    model_id = entry.model_id
                    provider_id = entry.provider_id

        # Drop history before the most recent compaction
        last_compaction: CompactionEntry | None = None
        for entry in entries:
            if isinstance(entry, CompactionEntry):
                last_compaction = entry

        if last_compaction is not None:
            first_kept_idx = next(
                (i for i, e in enumerate(entries) if e.id == last_compaction.first_kept_entry_id),
                len(entries),
            )
            slice_entries = entries[first_kept_idx:]
        else:
            slice_entries = entries

        for entry in slice_entries:
            match entry:
                case MessageEntry():
                    messages.append(entry.message)
                case CustomMessageEntry():
                    messages.append(CustomMessage.from_session(entry=entry))
                case BranchSummaryEntry():
                    from tau.message.types import BranchSummaryMessage

                    messages.append(
                        BranchSummaryMessage(
                            summary=entry.summary,
                            from_id=entry.from_id,
                            timestamp=entry.timestamp,
                        )
                    )
                case CompactionEntry():
                    messages.insert(
                        0,
                        CompactionSummaryMessage(
                            summary=entry.summary,
                            tokens_before=entry.tokens_before,
                            timestamp=entry.timestamp,
                        ),
                    )

        return SessionContext(
            messages=messages,
            thinking_level=thinking_level,
            model_id=model_id,
            provider_id=provider_id,
        )

    def get_header(self) -> SessionHeader | None:
        """Return the session header entry, or None if not found."""
        for entry in self.entries:
            if isinstance(entry, SessionHeader):
                return entry
        return None

    def get_entries(self) -> list[SessionEntry]:
        """Return all non-header entries in the session."""
        return [entry for entry in self.entries if not isinstance(entry, SessionHeader)]

    def get_tree(self) -> list[SessionTreeNode]:
        """Build a hierarchical tree structure of all entries."""
        node_map: dict[str, SessionTreeNode] = {}
        roots: list[SessionTreeNode] = []

        for entry in self.get_entries():
            label = self.labels_by_id.get(entry.id)
            label_timestamp = self.label_timestamps_by_id.get(entry.id)
            node_map[entry.id] = SessionTreeNode(
                entry=entry,
                children=[],
                label_timestamp=label_timestamp,
                label=label,
            )

        for entry in self.get_entries():
            node = node_map[entry.id]
            if entry.parent_id is None or entry.parent_id == entry.id:
                roots.append(node)
            else:
                parent_node = node_map.get(entry.parent_id)
                if parent_node is None:
                    roots.append(node)
                else:
                    parent_node.children.append(node)

        stack = roots.copy()
        while stack:
            node = stack.pop()
            node.children.sort(key=lambda child: child.entry.timestamp)
            stack.extend(node.children)

        roots.sort(key=lambda node: node.entry.timestamp)
        return roots

    def branch(self, from_id: str):
        """Navigate to a given entry and record the navigation point."""
        if from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found.")
        # Persist a LeafEntry so the navigation point survives restarts.
        leaf_entry = LeafEntry(parent_id=self.leaf_id, target_id=from_id)
        self.entries.append(leaf_entry)
        self.by_id[leaf_entry.id] = leaf_entry
        self._persist(leaf_entry)
        self.leaf_id = from_id

    def reset_leaf(self):
        """Clear the leaf pointer."""
        self.leaf_id = None

    def create_branched_session(self, leaf_id: str) -> Path | None:
        """Create a new session file forking from the given entry."""
        previous_session_file = self.session_file
        path = self.get_branch(leaf_id)

        if not path:
            raise ValueError(f"Entry {leaf_id} not found.")

        path_without_labels = [entry for entry in path if not isinstance(entry, LabelEntry)]

        session_id = create_session_id()
        file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        new_session_file = self.session_dir / f"{file_timestamp}_{session_id}.jsonl"

        header = SessionHeader(
            id=session_id,
            timestamp=generate_timestamp(),
            cwd=self.cwd,
            parent_session=previous_session_file if self.persist else None,
        )

        path_entry_ids = {entry.id for entry in path_without_labels}
        labels_to_write: list[tuple[str, str, float]] = [
            (target_id, label, self.label_timestamps_by_id[target_id])
            for target_id, label in self.labels_by_id.items()
            if target_id in path_entry_ids
        ]

        label_entries: list[LabelEntry] = []
        last_entry = path_without_labels[-1] if path_without_labels else None
        parent_id = last_entry.id if last_entry else None
        used_ids = set(path_entry_ids)

        for target_id, label, label_timestamp in labels_to_write:
            label_entry = LabelEntry(
                id=generate_id(used_ids),
                parent_id=parent_id,
                timestamp=label_timestamp,
                target_id=target_id,
                label=label,
            )
            used_ids.add(label_entry.id)
            label_entries.append(label_entry)
            parent_id = label_entry.id

        self.entries = [header, *path_without_labels, *label_entries]
        self.session_id = session_id
        self.session_file = new_session_file if self.persist else None
        self._build_index()

        has_assistant = any(
            isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage)
            for entry in self.entries
        )

        if self.persist:
            if has_assistant:
                self._rewrite_file()
                self.flushed = True
            else:
                self.flushed = False
            return new_session_file
        return None

    @classmethod
    def create(cls, cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        """Create a new SessionManager with a fresh session."""
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_project_session_dir(cwd)
        return SessionManager(cwd, session_dir)

    @staticmethod
    def open(
        path: Path | str,
        session_dir: Path | str | None = None,
        cwd_override: Path | str | None = None,
    ) -> SessionManager:
        """Load an existing session from a file."""
        path = Path(path).resolve()
        entries = read_session_file(path)
        header = next((e for e in entries if isinstance(e, SessionHeader)), None)
        if header is None:
            raise ValueError(f"No header found in session file: {path}")
        cwd = Path(cwd_override).resolve() if cwd_override else Path(header.cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else path.parent
        return SessionManager(cwd, session_dir, path)

    @staticmethod
    def continue_recent(cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        """Load the most recent session, or create a new one if none exist."""
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_project_session_dir(cwd)
        most_recent = find_most_recent_session(session_dir)
        if most_recent:
            return SessionManager(cwd, session_dir, most_recent)
        return SessionManager(cwd, session_dir)

    @staticmethod
    def in_memory(cwd: Path | None = None) -> SessionManager:
        """Create an in-memory session that is not persisted to disk."""
        cwd = cwd or Path.cwd()
        return SessionManager(cwd, None, None, False)

    @staticmethod
    def fork_from(
        source: Path | str,
        target_cwd: Path | str,
        session_dir: Path | str | None = None,
    ) -> SessionManager:
        """Create a new session forking from an existing session file."""
        source = Path(source).resolve()
        target_cwd = Path(target_cwd).resolve()
        source_entries = read_session_file(source)

        if not source_entries:
            raise ValueError(f"Cannot fork: source session file is empty or invalid: {source}")
        if not isinstance(source_entries[0], SessionHeader):
            raise ValueError(f"Cannot fork: source session has no header: {source}")

        session_dir = (
            Path(session_dir).resolve() if session_dir else get_default_project_session_dir(target_cwd)
        )
        session_dir.mkdir(parents=True, exist_ok=True)

        new_session_id = create_session_id()
        file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        new_session_file = session_dir / f"{file_timestamp}_{new_session_id}.jsonl"

        new_header = SessionHeader(
            id=new_session_id,
            timestamp=generate_timestamp(),
            cwd=target_cwd,
            parent_session=source,
        )

        with new_session_file.open("w", encoding="utf-8") as f:
            f.write(new_header.model_dump_json() + "\n")
            for entry in source_entries:
                if isinstance(entry, SessionHeader):
                    continue
                f.write(entry.model_dump_json() + "\n")

        return SessionManager(target_cwd, session_dir, new_session_file)

    @staticmethod
    def list(
        cwd: Path | str,
        session_dir: Path | str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[SessionInfo]:
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_project_session_dir(cwd)
        sessions = list_sessions_from_dir(session_dir, on_progress=on_progress)
        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions

    @staticmethod
    def list_all(
        on_progress: Callable[[int, int], None] | None = None,
    ) -> builtins.list[SessionInfo]:
        sessions_dir = get_sessions_dir()
        if not sessions_dir.exists():
            return []

        sessions: list[SessionInfo] = []
        try:
            for cwd_dir in sessions_dir.iterdir():
                if cwd_dir.is_dir():
                    dir_sessions = list_sessions_from_dir(cwd_dir, on_progress=on_progress)
                    sessions.extend(dir_sessions)
        except Exception:
            pass

        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions
