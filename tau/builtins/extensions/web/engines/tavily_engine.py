"""Tavily engine (LLM-oriented search, https://tavily.com).
Requires an API key + ``tavily-python``.
"""

from __future__ import annotations

import asyncio

from .base import BaseSearchEngine, SearchMode, SearchRecency, result


class TavilySearchEngine(BaseSearchEngine):
    name = "tavily"
    supported_modes = frozenset({SearchMode.text, SearchMode.news})
    supports_recency = True

    def __init__(self, api_key: str, search_depth: str = "basic") -> None:
        if not api_key:
            raise ValueError("Tavily engine requires an API key (set web settings tavily.api_key).")
        self._api_key = api_key
        self._search_depth = search_depth or "basic"
        self._client = None

    def _get_client(self):
        if self._client is None:
            from tavily import TavilyClient  # type: ignore[import-not-found]

            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    async def search(
        self,
        query: str,
        mode: SearchMode,
        max_results: int,
        recency: SearchRecency | None = None,
    ) -> list[dict]:
        def _search():
            client = self._get_client()
            topic = "news" if mode is SearchMode.news else "general"
            # Tavily accepts the same "day"/"week"/"month"/"year" literals we use.
            response = client.search(
                query,
                max_results=max_results,
                topic=topic,
                search_depth=self._search_depth,
                time_range=recency.value if recency else None,
            )
            out: list[dict] = []
            for r in response.get("results", []):
                out.append(
                    result(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("content", ""),
                        date=r.get("published_date", ""),
                    )
                )
            return out

        return await asyncio.to_thread(_search)

    async def fetch(self, url: str, timeout: int) -> str:
        def _fetch():
            client = self._get_client()
            response = client.extract(urls=[url], timeout=timeout)
            results = response.get("results", [])
            if not results:
                return ""
            return results[0].get("raw_content", "") or ""

        return await asyncio.to_thread(_fetch)
