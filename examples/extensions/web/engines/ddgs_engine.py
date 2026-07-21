"""DuckDuckGo engine — search/fetch/books via ddgs, run off-thread since it's sync."""

from __future__ import annotations

import asyncio

from .base import BaseSearchEngine, SearchMode, SearchRecency, result

# ddgs `timelimit` codes — stable since duckduckgo_search's earliest
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

    def _get_client(self):
        """Return a persistent ``DDGS`` client, creating it on first use.

        Reusing one instance across calls (rather than a fresh ``DDGS()`` per
        search) preserves whatever session/rate-limit state the client keeps
        internally, same rationale as the previous asyncddgs-based client.

        This project previously used ``asyncddgs`` (an early 0.1.0a1 alpha)
        for text/news/images/videos search. That client's ``_get_url()``
        passes ``allow_redirects=False`` and never checks the response status
        code before parsing it as a results page — so a redirect or bot
        challenge from DuckDuckGo (no session/cookie warmup precedes the
        search request) gets silently parsed as zero results, no exception,
        no log. ``ddgs`` (the actively-maintained sync package, already used
        here for books/fetch) doesn't have this gap, so all search modes now
        route through it via ``asyncio.to_thread`` instead.
        """
        if self._client is None:
            from ddgs import DDGS  # type: ignore[import-not-found]

            self._client = DDGS()
        return self._client

    async def aclose(self) -> None:
        self._client = None

    async def search(
        self,
        query: str,
        mode: SearchMode,
        max_results: int,
        recency: SearchRecency | None = None,
    ) -> list[dict]:
        region, safe = self._region, self._safesearch
        timelimit = _TIMELIMIT.get(recency) if recency else None
        d = self._get_client()

        match mode:
            case SearchMode.books:
                raw = await asyncio.to_thread(
                    lambda: d.books(query, region=region, safesearch=safe, max_results=max_results)
                    or []
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
            case SearchMode.text:
                raw = await asyncio.to_thread(
                    lambda: d.text(
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
                raw = await asyncio.to_thread(
                    lambda: d.news(
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
                raw = await asyncio.to_thread(
                    lambda: d.images(query, region=region, safesearch=safe, max_results=max_results)
                    or []
                )
                return [
                    result(title=r.get("title", ""), url=r.get("url", ""), image=r.get("image", ""))
                    for r in raw
                ]
            case SearchMode.videos:
                raw = await asyncio.to_thread(
                    lambda: d.videos(query, region=region, safesearch=safe, max_results=max_results)
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
        from ddgs import DDGS  # type: ignore[import-not-found]

        def _fetch() -> str:
            ddgs = DDGS(timeout=timeout)
            res = ddgs.extract(url)
            raw = res.get("content", "") or ""
            return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw

        return await asyncio.to_thread(_fetch)
