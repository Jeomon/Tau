"""Web search/fetch engines and the factory that selects one from settings.

Add a new backend by subclassing :class:`BaseSearchEngine` and registering it
in ``_BUILDERS`` below.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import BaseSearchEngine, SearchMode, result
from .ddgs_engine import DDGSearchEngine
from .exa_engine import ExaSearchEngine
from .jina_engine import JinaSearchEngine
from .tavily_engine import TavilySearchEngine

__all__ = [
    "BaseSearchEngine", "SearchMode", "result",
    "DDGSearchEngine", "ExaSearchEngine", "JinaSearchEngine", "TavilySearchEngine",
    "build_engine", "get_nested",
]


def get_nested(d: dict, path: str, default: Any = "") -> Any:
    """Read ``path`` (dot-notation) from a raw config dict, or ``default``."""
    obj: Any = d
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return default
        obj = obj[part]
    return obj if obj is not None else default


def _resolve_secret(value: str) -> str:
    """Resolve an api_key value: literal, ``$ENV_VAR``, or ``!shell-command``."""
    from tau.utils.secrets import resolve_secret
    return resolve_secret(value)


def _group(config: dict, name: str) -> dict:
    g = config.get(name)
    return g if isinstance(g, dict) else {}


def _build_ddgs(config: dict) -> BaseSearchEngine:
    c = _group(config, "ddgs")
    return DDGSearchEngine(
        region=c.get("region", "us-en"),
        safesearch=c.get("safesearch", "off"),
    )


def _build_exa(config: dict) -> BaseSearchEngine:
    c = _group(config, "exa")
    return ExaSearchEngine(
        _resolve_secret(c.get("api_key", "")),
        type=c.get("type", "auto"),
    )


def _build_tavily(config: dict) -> BaseSearchEngine:
    c = _group(config, "tavily")
    return TavilySearchEngine(
        _resolve_secret(c.get("api_key", "")),
        search_depth=c.get("search_depth", "basic"),
    )


def _build_jina(config: dict) -> BaseSearchEngine:
    c = _group(config, "jina")
    return JinaSearchEngine(
        api_key=_resolve_secret(c.get("api_key", "")),
        no_cache=bool(c.get("no_cache", False)),
    )


_BUILDERS: dict[str, Callable[[dict], BaseSearchEngine]] = {
    "ddgs":   _build_ddgs,
    "exa":    _build_exa,
    "tavily": _build_tavily,
    "jina":   _build_jina,
}


def build_engine(config: dict) -> BaseSearchEngine:
    """Construct the engine named by ``config["engine"]`` (default ``ddgs``).

    ``config`` is the raw extension settings dict. Falls back to the DDG engine
    if the configured engine is unknown or fails to initialize (e.g. a missing
    API key), so web search always works.
    """
    name = str(config.get("engine") or "ddgs").lower()
    builder = _BUILDERS.get(name)
    if builder is None:
        return DDGSearchEngine()
    try:
        return builder(config)
    except Exception:
        return DDGSearchEngine()
