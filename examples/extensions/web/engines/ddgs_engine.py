"""DuckDuckGo engine — async search via asyncddgs, fetch/books via ddgs."""

from __future__ import annotations

import asyncio

from .base import BaseSearchEngine, SearchMode, SearchRecency, result

# ddgs/asyncddgs `timelimit` codes — stable since duckduckgo_search's earliest
# releases: single-letter day/week/month/year.
_TIMELIMIT = {
    SearchRecency.day: "d",
    SearchRecency.week: "w",
    SearchRecency.month: "m",
    SearchRecency.year: "y",
}


class DDGSearchEngine(BaseSearchEngine):
    name = "ddgs"
    supported_modes = frozenset(
        {
            SearchMode.text,
            SearchMode.news,
            SearchMode.images,
            SearchMode.videos,
            SearchMode.books,
        }
    )
    supports_recency = True

    def __init__(self, region: str = "us-en", safesearch: str = "off") -> None:
        self._region = region or "us-en"
        self._safesearch = safesearch or "off"
        self._client = None  # lazily-created, reused across calls — see _get_client
        self._client_lock = asyncio.Lock()

    async def _get_client(self):
        """Return a persistent ``aDDGS`` client, creating it on first use.

        ``aDDGS`` keeps per-instance cookie and rate-limit state
        (``min_request_interval``) that only does anything useful if the same
        instance is reused across requests. Opening a fresh ``async with
        aDDGS()`` per search (the old behavior) reset that state every call,
        so a handful of searches in a row looked bot-like to DuckDuckGo and
        got served its 202 challenge page — which asyncddgs' parser silently
        treats as zero results rather than an error.
        """
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    from asyncddgs import aDDGS

                    client = aDDGS()
                    await client.__aenter__()
                    self._client = client
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.__aexit__(None, None, None)

    async def search(
        self,
        query: str,
        mode: SearchMode,
        max_results: int,
        recency: SearchRecency | None = None,
    ) -> list[dict]:
        region, safe = self._region, self._safesearch
        timelimit = _TIMELIMIT.get(recency) if recency else None

        if mode is SearchMode.books:
            from ddgs import DDGS

            raw = await asyncio.to_thread(
                lambda: DDGS().books(query, max_results=max_results) or []
            )
            return [
                result(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    author=r.get("author", ""),
                    publisher=r.get("publisher", ""),
                    info=r.get("info", ""),
                )
                for r in raw
            ]

        d = await self._get_client()
        match mode:
            case SearchMode.text:
                raw = (
                    await d.text(
                        query,
                        region=region,
                        safesearch=safe,
                        timelimit=timelimit,
                        max_results=max_results,
                    )
                    or []
                )
                return [
                    result(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        snippet=r.get("body", ""),
                    )
                    for r in raw
                ]
            case SearchMode.news:
                raw = (
                    await d.news(
                        query,
                        region=region,
                        safesearch=safe,
                        timelimit=timelimit,
                        max_results=max_results,
                    )
                    or []
                )
                return [
                    result(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("body", ""),
                        source=r.get("source", ""),
                        date=r.get("date", ""),
                    )
                    for r in raw
                ]
            case SearchMode.images:
                raw = (
                    await d.images(query, region=region, safesearch=safe, max_results=max_results)
                    or []
                )
                return [
                    result(title=r.get("title", ""), url=r.get("url", ""), image=r.get("image", ""))
                    for r in raw
                ]
            case SearchMode.videos:
                raw = (
                    await d.videos(query, region=region, safesearch=safe, max_results=max_results)
                    or []
                )
                return [
                    result(
                        title=r.get("title", ""),
                        url=r.get("content", ""),
                        snippet=r.get("description", ""),
                        duration=r.get("duration", ""),
                    )
                    for r in raw
                ]

    async def fetch(self, url: str, timeout: int) -> str:
        from ddgs import DDGS

        def _fetch() -> str:
            ddgs = DDGS(timeout=timeout)
            res = ddgs.extract(url)
            raw = res.get("content", "") or ""
            return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw

        return await asyncio.to_thread(_fetch)
