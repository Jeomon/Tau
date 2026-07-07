"""Base contract for web search/fetch engines.

Every concrete engine (DDG, Exa, Tavily, …) maps its provider-specific output
into one *normalized* result dict so the rest of the extension — formatting,
rendering, metadata — never depends on which engine produced the results.

Normalized result keys (all optional, default ""):
    title      Display title of the result.
    url        Canonical link to open / fetch.
    snippet    Short text summary / body.
    source     Publisher or site name (news).
    date       Publication date (news).
    image      Direct image URL (images).
    duration   Video length (videos).
    author     Author name (books).
    publisher  Publisher (books).
    info       Extra free-form info (books).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum


class SearchMode(StrEnum):
    text = "text"
    news = "news"
    images = "images"
    videos = "videos"
    books = "books"


class SearchRecency(StrEnum):
    day = "day"
    week = "week"
    month = "month"
    year = "year"


_RESULT_KEYS = (
    "title",
    "url",
    "snippet",
    "source",
    "date",
    "image",
    "duration",
    "author",
    "publisher",
    "info",
)


def result(**fields) -> dict:
    """Build a normalized result dict, filling absent keys with ""."""
    return {key: fields.get(key, "") for key in _RESULT_KEYS}


class BaseSearchEngine(ABC):
    """A web search + fetch backend.

    Subclasses set ``name`` and ``supported_modes`` and implement ``search``
    and ``fetch``. ``search`` must return a list of normalized dicts (see
    :func:`result`); ``fetch`` returns page content as plain text.
    """

    name: str = "base"
    supported_modes: frozenset[SearchMode] = frozenset({SearchMode.text})
    # Whether this engine can restrict results to a recency window (see
    # SearchRecency). Engines without native date filtering (e.g. Jina) leave
    # this False; the tool then warns instead of silently ignoring the filter.
    supports_recency: bool = False

    def supports(self, mode: SearchMode) -> bool:
        return mode in self.supported_modes

    @abstractmethod
    async def search(
        self,
        query: str,
        mode: SearchMode,
        max_results: int,
        recency: SearchRecency | None = None,
    ) -> list[dict]:
        """Run a search and return normalized result dicts.

        ``recency`` is a best-effort filter — pass it through when
        ``supports_recency`` is True; engines that can't honor it should
        ignore it rather than error, since the caller already warns the
        model when the active engine doesn't support it.
        """

    @abstractmethod
    async def fetch(self, url: str, timeout: int) -> str:
        """Fetch a URL and return its content as text."""
