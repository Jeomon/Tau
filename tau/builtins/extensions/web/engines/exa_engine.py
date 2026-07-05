"""Exa engine (neural search, https://exa.ai). Requires an API key + ``exa-py``."""

from __future__ import annotations

import asyncio

from .base import BaseSearchEngine, SearchMode, result


class ExaSearchEngine(BaseSearchEngine):
    name = "exa"
    supported_modes = frozenset({SearchMode.text, SearchMode.news})

    def __init__(self, api_key: str, type: str = "auto") -> None:
        if not api_key:
            raise ValueError("Exa engine requires an API key (set web settings exa.api_key).")
        self._api_key = api_key
        self._type = type or "auto"
        self._client = None

    def _get_client(self):
        if self._client is None:
            from exa_py import Exa  # type: ignore[import-not-found]

            self._client = Exa(self._api_key)
        return self._client

    async def search(self, query: str, mode: SearchMode, max_results: int) -> list[dict]:
        def _search():
            client = self._get_client()
            category = "news" if mode is SearchMode.news else None
            response = client.search_and_contents(
                query,
                num_results=max_results,
                category=category,
                type=self._type,
                text={"max_characters": 500},
            )
            out: list[dict] = []
            for r in response.results:
                snippet = (getattr(r, "text", "") or "").strip()
                out.append(
                    result(
                        title=getattr(r, "title", "") or "",
                        url=getattr(r, "url", "") or "",
                        snippet=snippet,
                        source=getattr(r, "author", "") or "",
                        date=getattr(r, "published_date", "") or "",
                    )
                )
            return out

        return await asyncio.to_thread(_search)

    async def fetch(self, url: str, timeout: int) -> str:
        def _fetch():
            client = self._get_client()
            response = client.get_contents([url], text=True)
            if not response.results:
                return ""
            return getattr(response.results[0], "text", "") or ""

        return await asyncio.to_thread(_fetch)
