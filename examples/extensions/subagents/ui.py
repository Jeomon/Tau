from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from tau.message.types import TextContent
from tau.session.types import MessageEntry
from tau.session.utils import read_session_file
from tau.tui.input import InputEvent, KeyEvent

from .types import AgentRecord, AgentStatus

_ACTIVE_STATUSES = {AgentStatus.QUEUED, AgentStatus.RUNNING}


class AgentWidget:
    """Compact live summary rendered above the editor while agents are active."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def render(self, width: int) -> list[str]:
        records = [
            record for record in self._manager.list_records() if record.status in _ACTIVE_STATUSES
        ]
        if not records:
            return []
        lines = [f"  Agents ({len(records)})"]
        for record in records[:5]:
            icon = "○" if record.status == AgentStatus.QUEUED else "●"
            stats = f"{record.turn_count} turns · {record.tool_uses} tools"
            line = f"  {icon} {record.agent_type}  {record.description}  {stats}"
            lines.append(line[:width])
        if len(records) > 5:
            lines.append(f"  ↓ {len(records) - 5} more")
        return lines

    def handle_input(self, _event: InputEvent) -> bool:
        return False

    def invalidate(self) -> None:
        pass


def _message_lines(session_file: Path) -> list[str]:
    try:
        entries = read_session_file(session_file)
    except OSError:
        return ["Unable to read transcript."]
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        role = str(entry.message.role)
        text = "".join(
            content.content
            for content in entry.message.contents
            if isinstance(content, TextContent)
        ).strip()
        if not text:
            continue
        lines.append(f"{role}:")
        lines.extend(f"  {line}" for line in text.splitlines())
        lines.append("")
    return lines or ["(no conversation output yet)"]


class ConversationViewer:
    """Scrollable transcript overlay that reloads the session on every render."""

    def __init__(self, record: AgentRecord, done: Callable[[None], None]) -> None:
        self._record = record
        self._done = done
        self._offset = 0

    def render(self, width: int) -> list[str]:
        status = self._record.status.value
        title = f"  {self._record.agent_type} · {self._record.id} · {status}"
        session_file = self._record.output_file
        content = (
            _message_lines(session_file)
            if session_file is not None and session_file.exists()
            else ["(transcript not available yet)"]
        )
        height = 24
        max_offset = max(0, len(content) - height)
        self._offset = min(self._offset, max_offset)
        start = max(0, max_offset - self._offset)
        visible = content[start : start + height]
        footer = "  ↑/↓ scroll · PgUp/PgDn page · Esc close"
        return [
            title[:width],
            "─" * width,
            *[line[:width] for line in visible],
            "─" * width,
            footer,
        ]

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return True
        match event.key:
            case "escape":
                self._done(None)
            case "up":
                self._offset += 1
            case "down":
                self._offset = max(0, self._offset - 1)
            case "page_up" | "pageup":
                self._offset += 10
            case "page_down" | "pagedown":
                self._offset = max(0, self._offset - 10)
        return True

    def invalidate(self) -> None:
        pass
