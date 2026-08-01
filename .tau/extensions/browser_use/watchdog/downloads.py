"""Translate CDP download events into browser hooks."""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import unquote, urlsplit

from ..browser.hooks import (
    BrowserEvent,
    BrowserReconnectedEvent,
    BrowserStartedEvent,
    BrowserStopEvent,
    DownloadProgressEvent,
    DownloadStartedEvent,
    FileDownloadedEvent,
)

from .base import BaseWatchdog

# Mime types Chrome renders inline (e.g. in its built-in PDF viewer) instead of
# triggering Browser.downloadWillBegin, even with download behavior set to
# "allow". Sniffed from Network.responseReceived so these still get saved.
_INLINE_DOWNLOAD_MIME_TYPES = {
    "application/pdf": ".pdf",
}


class DownloadsWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        BrowserStartedEvent,
        BrowserReconnectedEvent,
        BrowserStopEvent,
    )
    EMITS: ClassVar[tuple[type[BrowserEvent], ...]] = (
        DownloadStartedEvent,
        DownloadProgressEvent,
        FileDownloadedEvent,
    )

    def __init__(self, browser) -> None:
        super().__init__(browser)
        self._downloads: dict[str, dict[str, Any]] = {}
        self._pending_inline: dict[tuple[str, str], dict[str, Any]] = {}

    async def on_BrowserStartedEvent(self, _event: BrowserStartedEvent) -> None:
        await self._attach()

    async def on_BrowserReconnectedEvent(
        self, _event: BrowserReconnectedEvent
    ) -> None:
        await self._attach()

    async def _attach(self) -> None:
        client = self.browser.client
        if client is None:
            return
        if self.browser.settings.downloads_path is None:
            try:
                await client.browser.set_download_behavior(
                    {"behavior": "default", "eventsEnabled": True}
                )
            except Exception:
                # Some real (non-automation-launched) Chrome instances reject
                # this with "Browser context management is not supported."
                # Download event tracking degrades gracefully without it.
                self.logger.warning(
                    "failed to set download behavior; download tracking may "
                    "be unavailable on this browser connection",
                    exc_info=True,
                )
        client.register("Browser.downloadWillBegin", self._on_download_started)
        client.register("Browser.downloadProgress", self._on_download_progress)
        client.register("Network.responseReceived", self._on_response_received)
        client.register("Network.loadingFinished", self._on_loading_finished)

    async def on_BrowserStopEvent(self, _event: BrowserStopEvent) -> None:
        client = self.browser.client
        if client is not None:
            client.unregister("Browser.downloadWillBegin", self._on_download_started)
            client.unregister("Browser.downloadProgress", self._on_download_progress)
            client.unregister("Network.responseReceived", self._on_response_received)
            client.unregister("Network.loadingFinished", self._on_loading_finished)
        self._downloads.clear()
        self._pending_inline.clear()

    def _on_download_started(
        self, params: dict[str, Any], _session_id: str | None
    ) -> None:
        guid = params["guid"]
        self._downloads[guid] = params
        target_id = self.browser.session.target_for_frame(
            params.get("frameId", "")
        )
        self.browser.downloads.started(
            guid=guid,
            url=params.get("url", ""),
            suggested_filename=params.get("suggestedFilename", ""),
            target_id=target_id,
        )
        self.create_task(
            self.browser.hooks.emit(
                DownloadStartedEvent(
                    guid=guid,
                    url=params.get("url", ""),
                    suggested_filename=params.get("suggestedFilename", ""),
                    target_id=target_id,
                )
            ),
            name=f"download-started-{guid}",
        )

    def _on_download_progress(
        self, params: dict[str, Any], _session_id: str | None
    ) -> None:
        guid = params["guid"]
        state = params.get("state", "inProgress")
        metadata = self._downloads.get(guid, {})
        self.browser.downloads.progress(
            guid=guid,
            received_bytes=int(params.get("receivedBytes", 0)),
            total_bytes=int(params.get("totalBytes", 0)),
            state=state,
            file_path=params.get("filePath"),
        )
        self.create_task(
            self._emit_progress(params, metadata),
            name=f"download-progress-{guid}",
        )
        if state in {"completed", "canceled"}:
            self._downloads.pop(guid, None)

    async def _emit_progress(
        self, params: dict[str, Any], metadata: dict[str, Any]
    ) -> None:
        guid = params["guid"]
        state = params.get("state", "inProgress")
        received = int(params.get("receivedBytes", 0))
        await self.browser.hooks.emit(
            DownloadProgressEvent(
                guid=guid,
                received_bytes=received,
                total_bytes=int(params.get("totalBytes", 0)),
                state=state,
            )
        )
        path = params.get("filePath")
        if state == "completed" and path:
            await self.browser.hooks.emit(
                FileDownloadedEvent(
                    guid=guid,
                    url=metadata.get("url", ""),
                    path=path,
                    file_name=Path(path).name,
                    file_size=received,
                )
            )

    def _on_response_received(
        self, params: dict[str, Any], session_id: str | None
    ) -> None:
        if session_id is None or self.browser.settings.downloads_path is None:
            return
        if params.get("type") != "Document":
            return
        response = params.get("response", {})
        mime_type = response.get("mimeType", "").split(";")[0].strip().lower()
        extension = _INLINE_DOWNLOAD_MIME_TYPES.get(mime_type)
        if extension is None:
            return
        self._pending_inline[(session_id, params["requestId"])] = {
            "url": response.get("url", params.get("documentURL", "")),
            "headers": response.get("headers", {}),
            "extension": extension,
        }

    def _on_loading_finished(
        self, params: dict[str, Any], session_id: str | None
    ) -> None:
        if session_id is None:
            return
        key = (session_id, params["requestId"])
        candidate = self._pending_inline.pop(key, None)
        if candidate is None:
            return
        self.create_task(
            self._save_inline_download(session_id, params["requestId"], candidate),
            name=f"inline-download-{params['requestId']}",
        )

    async def _save_inline_download(
        self, session_id: str, request_id: str, candidate: dict[str, Any]
    ) -> None:
        client = self.browser.client
        directory = self.browser.settings.downloads_path
        if client is None or directory is None:
            return
        try:
            body = await client.network.get_response_body(
                {"requestId": request_id}, session_id=session_id
            )
        except Exception:
            self.logger.exception(
                "failed to fetch inline document body for %s", candidate["url"]
            )
            return

        data = body.get("body", "")
        content = base64.b64decode(data) if body.get("base64Encoded") else data.encode(
            "utf-8", errors="replace"
        )

        file_name = _inline_download_filename(candidate["headers"], candidate["url"], candidate["extension"])
        destination = Path(directory).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / file_name

        guid = f"inline-{uuid.uuid4().hex}"
        target_id = self.browser.session.target_for_session(session_id)
        self.browser.downloads.started(
            guid=guid,
            url=candidate["url"],
            suggested_filename=file_name,
            target_id=target_id,
        )
        await self.browser.hooks.emit(
            DownloadStartedEvent(
                guid=guid,
                url=candidate["url"],
                suggested_filename=file_name,
                auto_download=True,
                target_id=target_id,
            )
        )

        try:
            path.write_bytes(content)
        except OSError:
            self.logger.exception("failed to write inline download to %s", path)
            return

        self.browser.downloads.progress(
            guid=guid,
            received_bytes=len(content),
            total_bytes=len(content),
            state="completed",
            file_path=str(path),
        )
        await self.browser.hooks.emit(
            FileDownloadedEvent(
                guid=guid,
                url=candidate["url"],
                path=str(path),
                file_name=path.name,
                file_size=len(content),
                mime_type=None,
                auto_download=True,
            )
        )


def _inline_download_filename(
    headers: dict[str, Any], url: str, extension: str
) -> str:
    for key, value in headers.items():
        if key.lower() != "content-disposition":
            continue
        marker = "filename="
        lowered = value.lower()
        index = lowered.find(marker)
        if index != -1:
            candidate = value[index + len(marker) :].split(";")[0].strip().strip('"')
            if candidate:
                return unquote(candidate)

    name = Path(urlsplit(url).path).name
    if name:
        return name if Path(name).suffix else f"{name}{extension}"
    return f"download{extension}"
