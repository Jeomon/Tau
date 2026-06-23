"""Jina AI Reader engine — fetch via r.jina.ai, search via s.jina.ai.

No extra dependencies required (uses httpx, already in the project).
An optional API key unlocks higher rate limits and the internal proxy.
Obtain one at https://jina.ai/reader.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from .base import BaseSearchEngine, SearchMode, result

_READER_URL = "https://r.jina.ai/"
_SEARCH_URL = "https://s.jina.ai/"


class JinaSearchEngine(BaseSearchEngine):
    name = "jina"
    supported_modes = frozenset({SearchMode.text})

    def __init__(self, api_key: str = "", no_cache: bool = False) -> None:
        self._api_key = api_key or ""
        self._no_cache = no_cache

    def _headers(self, *, json: bool = False) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json" if json else "text/plain"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        if self._no_cache:
            h["X-No-Cache"] = "true"
        return h

    async def search(self, query: str, mode: SearchMode, max_results: int) -> list[dict]:
        url = f"{_SEARCH_URL}{quote(query, safe='')}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=self._headers(json=True))
            response.raise_for_status()
            data = response.json()

        results_raw = data.get("data", [])
        if not isinstance(results_raw, list):
            return []

        out: list[dict] = []
        for r in results_raw[:max_results]:
            content = r.get("content", "") or ""
            snippet = r.get("description", "") or content[:300]
            out.append(result(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=snippet,
            ))
        return out

    async def fetch(self, url: str, timeout: int) -> str:
        jina_url = f"{_READER_URL}{url}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(jina_url, headers=self._headers())
            response.raise_for_status()
            return response.text
