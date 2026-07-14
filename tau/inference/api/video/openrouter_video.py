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

_BASE = "https://openrouter.ai/api/v1"


class OpenRouterVideoAPI(BaseVideoAPI):
    """
    OpenRouter video generation API.

    Submits a generation job, then polls until completion or timeout.
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
        payload: dict[str, Any] = {
            "model": model.id,
            "prompt": context.prompt,
        }
        if context.aspect_ratio is not None:
            payload["aspect_ratio"] = context.aspect_ratio
        if context.duration is not None:
            payload["duration"] = context.duration
        if context.resolution is not None:
            payload["resolution"] = context.resolution
        if context.image is not None:
            import base64

            payload["image_url"] = (
                f"data:image/jpeg;base64,{base64.b64encode(context.image).decode()}"
            )
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

        from tau.utils.ssl_context import get_shared_ssl_context

        async with httpx.AsyncClient(timeout=60.0, verify=get_shared_ssl_context()) as client:
            # Submit job
            resp = await client.post(
                f"{base_url}/video/generations",
                json=payload,
                headers=headers,
            )
            if self.options.on_response:
                self.options.on_response(resp)

            if not resp.is_success:
                return GeneratedVideo(
                    model_id=model.id,
                    provider="openrouter",
                    stop_reason=VideoStopReason.Error,
                    error=f"HTTP {resp.status_code}: {resp.text}",
                )

            data = resp.json()
            job_id: str = data.get("id", "")

            # Poll for completion
            status_url = f"{base_url}/video/generations/{job_id}"

            while time.monotonic() < deadline:
                await asyncio.sleep(self.options.poll_interval)

                status_resp = await client.get(status_url, headers=headers)
                if not status_resp.is_success:
                    return GeneratedVideo(
                        model_id=model.id,
                        provider="openrouter",
                        stop_reason=VideoStopReason.Error,
                        error=f"HTTP {status_resp.status_code}: {status_resp.text}",
                    )

                result = status_resp.json()
                status = result.get("status", "")

                if status == "completed":
                    video_url: str | None = None
                    video_bytes: bytes | None = None

                    video_data = result.get("video") or {}
                    if isinstance(video_data, dict):
                        video_url = video_data.get("url")
                    elif isinstance(video_data, str):
                        video_url = video_data

                    if video_url:
                        dl = await client.get(video_url, follow_redirects=True, timeout=120.0)
                        dl.raise_for_status()
                        video_bytes = dl.content

                    return GeneratedVideo(
                        model_id=model.id,
                        provider="openrouter",
                        url=video_url,
                        video=video_bytes,
                        format=VideoFormat.MP4,
                        duration=result.get("duration") or context.duration,
                        stop_reason=VideoStopReason.Stop,
                    )

                if status == "failed":
                    error = result.get("error") or {}
                    msg = (
                        error.get("message", "Job failed")
                        if isinstance(error, dict)
                        else str(error)
                    )
                    return GeneratedVideo(
                        model_id=model.id,
                        provider="openrouter",
                        stop_reason=VideoStopReason.Error,
                        error=msg,
                    )

        return GeneratedVideo(
            model_id=model.id,
            provider="openrouter",
            stop_reason=VideoStopReason.Timeout,
            error=f"Video generation timed out after {timeout:.0f}s",
        )
