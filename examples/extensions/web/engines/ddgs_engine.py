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

        from asyncddgs import aDDGS

        async with aDDGS() as d:
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
                        await d.images(
                            query, region=region, safesearch=safe, max_results=max_results
                        )
                        or []
                    )
                    return [
                        result(
                            title=r.get("title", ""), url=r.get("url", ""), image=r.get("image", "")
                        )
                        for r in raw
                    ]
                case SearchMode.videos:
                    raw = (
                        await d.videos(
                            query, region=region, safesearch=safe, max_results=max_results
                        )
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
