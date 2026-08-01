"""Page console/log observability via the CDP Log and Runtime domains."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import SessionID


@dataclass(frozen=True, slots=True)
class ConsoleMessage:
    session_id: SessionID
    level: str
    text: str
    source: str = "console-api"
    url: str | None = None
    line_number: int | None = None


class Console:
    """Track console output and page-log entries emitted by page JavaScript."""

    def __init__(self, browser: Any, *, max_messages: int = 500) -> None:
        self.browser = browser
        self.max_messages = max_messages
        self.messages: list[ConsoleMessage] = []
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        client = self._client()
        client.register("Log.entryAdded", self._on_log_entry)
        client.register("Runtime.consoleAPICalled", self._on_console_api)
        self._started = True
        for session_id in tuple(self.browser.session.session_to_target):
            await self.configure_session(session_id)

    async def stop(self) -> None:
        client = self.browser.client
        if client is not None and self._started:
            client.unregister("Log.entryAdded", self._on_log_entry)
            client.unregister("Runtime.consoleAPICalled", self._on_console_api)
        self._started = False
        self.messages.clear()

    async def configure_session(self, session_id: SessionID) -> None:
        if not self._started:
            return
        await self._client().log.enable(session_id=session_id)

    def for_session(self, session_id: SessionID) -> list[ConsoleMessage]:
        return [message for message in self.messages if message.session_id == session_id]

    def clear(self, session_id: SessionID | None = None) -> None:
        if session_id is None:
            self.messages.clear()
        else:
            self.messages = [m for m in self.messages if m.session_id != session_id]

    def _on_log_entry(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        entry = params.get("entry", {})
        self._record(
            ConsoleMessage(
                session_id=session_id,
                level=entry.get("level", "info"),
                text=entry.get("text", ""),
                source=entry.get("source", "other"),
                url=entry.get("url"),
                line_number=entry.get("lineNumber"),
            )
        )

    def _on_console_api(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        args = params.get("args", [])
        text = " ".join(
            str(arg.get("value", arg.get("description", ""))) for arg in args
        )
        frames = params.get("stackTrace", {}).get("callFrames", [])
        top_frame = frames[0] if frames else {}
        self._record(
            ConsoleMessage(
                session_id=session_id,
                level=params.get("type", "log"),
                text=text,
                source="console-api",
                url=top_frame.get("url"),
                line_number=top_frame.get("lineNumber"),
            )
        )

    def _record(self, message: ConsoleMessage) -> None:
        self.messages.append(message)
        overflow = len(self.messages) - self.max_messages
        if overflow > 0:
            del self.messages[:overflow]

    def _client(self):
        if self.browser.client is None:
            raise RuntimeError("browser is not connected")
        return self.browser.client
