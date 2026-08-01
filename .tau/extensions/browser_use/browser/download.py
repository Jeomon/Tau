"""Download models and expectation registry."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .types import (
    DownloadCancelledError,
    DownloadPathError,
    DownloadTimeoutError,
    TargetID,
)

if TYPE_CHECKING:
    from .service import Browser

DownloadState = Literal["inProgress", "completed", "canceled", "failed"]
DownloadAction = Callable[[], Awaitable[Any] | Any]


class Download:
    def __init__(
        self,
        *,
        guid: str,
        url: str,
        suggested_filename: str,
        target_id: TargetID | None,
    ) -> None:
        self.guid = guid
        self.url = url
        self.suggested_filename = suggested_filename
        self.target_id = target_id
        self.received_bytes = 0
        self.total_bytes = 0
        self.state: DownloadState = "inProgress"
        self.path: Path | None = None
        self.error: BaseException | None = None
        self._finished = asyncio.Event()

    @property
    def is_finished(self) -> bool:
        return self._finished.is_set()

    async def wait(self, timeout: float | None = None) -> Download:
        try:
            if timeout is None:
                await self._finished.wait()
            else:
                await asyncio.wait_for(self._finished.wait(), timeout)
        except TimeoutError as exc:
            raise DownloadTimeoutError(
                f"download {self.guid} did not finish within {timeout:g} seconds"
            ) from exc
        if self.error:
            raise self.error
        return self

    def update(
        self,
        *,
        received_bytes: int,
        total_bytes: int,
        state: str,
        path: Path | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.received_bytes = received_bytes
        self.total_bytes = total_bytes
        if path is not None:
            self.path = path
        if error is not None:
            self.error = error
            self.state = "failed"
            self._finished.set()
        elif state == "canceled":
            self.state = "canceled"
            self.error = DownloadCancelledError(f"download {self.guid} was canceled")
            self._finished.set()
        elif state == "completed":
            self.state = "completed"
            self._finished.set()
        else:
            self.state = "inProgress"


class DownloadRegistry:
    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self._downloads: dict[str, Download] = {}
        self._expectations: list[tuple[TargetID | None, asyncio.Future[Download]]] = []
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    def get(self, guid: str) -> Download | None:
        return self._downloads.get(guid)

    def all(self) -> tuple[Download, ...]:
        return tuple(self._downloads.values())

    async def expect(
        self,
        target_id: TargetID | None,
        action: DownloadAction,
        *,
        timeout: float = 30.0,
    ) -> Download:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        future = asyncio.get_running_loop().create_future()
        expectation = (target_id, future)
        self._expectations.append(expectation)
        try:
            result = action()
            if inspect.isawaitable(result):
                await result
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise DownloadTimeoutError(
                f"download did not start within {timeout:g} seconds"
            ) from exc
        finally:
            if expectation in self._expectations:
                self._expectations.remove(expectation)

    def started(
        self,
        *,
        guid: str,
        url: str,
        suggested_filename: str,
        target_id: TargetID | None,
    ) -> Download:
        download = self._downloads.get(guid)
        if download is None:
            download = Download(
                guid=guid,
                url=url,
                suggested_filename=suggested_filename,
                target_id=target_id,
            )
            self._downloads[guid] = download
        expectation = self._matching_expectation(target_id)
        if expectation and not expectation.done():
            expectation.set_result(download)
        return download

    def progress(
        self,
        *,
        guid: str,
        received_bytes: int,
        total_bytes: int,
        state: str,
        file_path: str | None = None,
    ) -> Download:
        download = self._downloads.get(guid)
        if download is None:
            download = self.started(
                guid=guid,
                url="",
                suggested_filename="",
                target_id=None,
            )
        path: Path | None = None
        error: BaseException | None = None
        if file_path:
            path = Path(file_path).expanduser().resolve()
            directory = self.browser.settings.downloads_path
            if directory is not None:
                root = Path(directory).expanduser().resolve()
                if not path.is_relative_to(root):
                    error = DownloadPathError(
                        f"download path {path} is outside configured directory {root}"
                    )
        download.update(
            received_bytes=received_bytes,
            total_bytes=total_bytes,
            state=state,
            path=path,
            error=error,
        )
        if download.is_finished:
            self._schedule_cleanup(guid)
        return download

    def clear_completed(self) -> None:
        self._downloads = {
            guid: download
            for guid, download in self._downloads.items()
            if not download.is_finished
        }

    def close(self) -> None:
        error = DownloadCancelledError("browser closed during download")
        for download in self._downloads.values():
            if not download.is_finished:
                download.update(
                    received_bytes=download.received_bytes,
                    total_bytes=download.total_bytes,
                    state="failed",
                    error=error,
                )
        for _, future in self._expectations:
            if not future.done():
                future.set_exception(error)
        self._expectations.clear()
        for task in tuple(self._cleanup_tasks):
            task.cancel()
        self._cleanup_tasks.clear()

    def _matching_expectation(
        self, target_id: TargetID | None
    ) -> asyncio.Future[Download] | None:
        for expected_target, future in self._expectations:
            if expected_target == target_id:
                return future
        for _, future in self._expectations:
            if not future.done():
                return future
        return None

    def _schedule_cleanup(self, guid: str) -> None:
        retention = self.browser.settings.download_retention
        if retention == 0:
            self._downloads.pop(guid, None)
            return
        task = asyncio.create_task(
            self._cleanup_after(guid, retention),
            name=f"download-cleanup-{guid}",
        )
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _cleanup_after(self, guid: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self._downloads.pop(guid, None)
