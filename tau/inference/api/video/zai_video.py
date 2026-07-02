from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from tau.inference.api.video.base import BaseVideoAPI
from tau.inference.model.types import Model
from tau.inference.types import (
    GeneratedVideo,
    VideoContext,
    VideoFormat,
    VideoOptions,
    VideoStopReason,
)

_BASE = "https://api.z.ai/api/paas/v4"


class ZaiVideoAPI(BaseVideoAPI):
    """
    Z.ai video generation API (CogVideoX, Vidu) — POST /videos/generations
    returns a task id, then GET /async-result/{id} is polled until
    task_status is SUCCESS or FAIL. See docs.z.ai/api-reference/video.
    """

    def __init__(self, options: VideoOptions) -> None:
        super().__init__(options)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.options.api_key or ''}",
            "Content-Type": "application/json",
        }
        if self.options.headers:
            headers.update(self.options.headers)
        return headers

    def _build_payload(self, model: Model, context: VideoContext) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model.id}
        if context.prompt:
            payload["prompt"] = context.prompt
        if context.duration is not None:
            payload["duration"] = int(context.duration)
        if context.resolution is not None:
            payload["size"] = context.resolution
        if context.image is not None:
            import base64

            payload["image_url"] = [
                f"data:image/jpeg;base64,{base64.b64encode(context.image).decode()}"
            ]
        return payload

    async def generate(self, model: Model, context: VideoContext) -> GeneratedVideo:
        base_url = (self.options.base_url or _BASE).rstrip("/")
        timeout = self.options.timeout.total_seconds()
        deadline = time.monotonic() + timeout
        headers = self._headers()
        payload = self._build_payload(model, context)

        if self.options.on_payload:
            modified = self.options.on_payload(payload)
            if modified is not None:
                payload = modified

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/videos/generations", json=payload, headers=headers
            )
            if self.options.on_response:
                self.options.on_response(resp)

            if not resp.is_success:
                return GeneratedVideo(
                    model_id=model.id,
                    provider="zai",
                    stop_reason=VideoStopReason.Error,
                    error=f"HTTP {resp.status_code}: {resp.text}",
                )

            data = resp.json()
            task_id: str = data.get("id", "")

            status_url = f"{base_url}/async-result/{task_id}"

            while time.monotonic() < deadline:
                await asyncio.sleep(self.options.poll_interval)

                status_resp = await client.get(status_url, headers=headers)
                if not status_resp.is_success:
                    return GeneratedVideo(
                        model_id=model.id,
                        provider="zai",
                        stop_reason=VideoStopReason.Error,
                        error=f"HTTP {status_resp.status_code}: {status_resp.text}",
                    )

                result = status_resp.json()
                status = result.get("task_status", "")

                if status == "SUCCESS":
                    video_result = result.get("video_result") or []
                    video_url: str | None = None
                    if video_result and isinstance(video_result[0], dict):
                        video_url = video_result[0].get("url")

                    video_bytes: bytes | None = None
                    if video_url:
                        dl = await client.get(video_url, follow_redirects=True, timeout=120.0)
                        dl.raise_for_status()
                        video_bytes = dl.content

                    return GeneratedVideo(
                        model_id=model.id,
                        provider="zai",
                        url=video_url,
                        video=video_bytes,
                        format=VideoFormat.MP4,
                        duration=context.duration,
                        stop_reason=VideoStopReason.Stop,
                    )

                if status == "FAIL":
                    return GeneratedVideo(
                        model_id=model.id,
                        provider="zai",
                        stop_reason=VideoStopReason.Error,
                        error=str(result.get("fail_reason") or "Job failed"),
                    )

        return GeneratedVideo(
            model_id=model.id,
            provider="zai",
            stop_reason=VideoStopReason.Timeout,
            error=f"Video generation timed out after {timeout:.0f}s",
        )
