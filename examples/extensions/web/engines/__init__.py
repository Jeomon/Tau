"""Web search/fetch engines and the factory that selects one from settings.

Add a new backend by subclassing :class:`BaseSearchEngine` and registering it
in ``_BUILDERS`` below.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .base import BaseSearchEngine, SearchMode, SearchRecency, result

if TYPE_CHECKING:
    from .ddgs_engine import DDGSearchEngine
    from .exa_engine import ExaSearchEngine
    from .jina_engine import JinaSearchEngine
    from .tavily_engine import TavilySearchEngine

__all__ = [
    "BaseSearchEngine",
    "SearchMode",
    "SearchRecency",
    "result",
    "DDGSearchEngine",
    "ExaSearchEngine",
    "JinaSearchEngine",
    "TavilySearchEngine",
    "build_engine",
    "get_nested",
]


def __getattr__(name: str) -> Any:
    """Lazily import each concrete engine class on first access (PEP 562).

    Only ``build_engine()`` actually needs one of these (whichever
    ``config["engine"]`` names, default ``ddgs``) — importing all four
    eagerly here paid every backend's dependency cost on every startup
    (measured: ~130ms for ``jina_engine``'s ``httpx`` import alone) even
    though at most one is ever used per session. Keeps
    ``from tau.builtins.extensions.web.engines import JinaSearchEngine``
    working for anything that still spells it that way.
    """
    if name == "DDGSearchEngine":
        from .ddgs_engine import DDGSearchEngine

        return DDGSearchEngine
    if name == "ExaSearchEngine":
        from .exa_engine import ExaSearchEngine

        return ExaSearchEngine
    if name == "JinaSearchEngine":
        from .jina_engine import JinaSearchEngine

        return JinaSearchEngine
    if name == "TavilySearchEngine":
        from .tavily_engine import TavilySearchEngine

        return TavilySearchEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    from .ddgs_engine import DDGSearchEngine

    c = _group(config, "ddgs")
    return DDGSearchEngine(
        region=c.get("region", "us-en"),
        safesearch=c.get("safesearch", "off"),
    )


def _build_exa(config: dict) -> BaseSearchEngine:
    from .exa_engine import ExaSearchEngine

    c = _group(config, "exa")
    return ExaSearchEngine(
        _resolve_secret(c.get("api_key", "")),
        type=c.get("type", "auto"),
    )


def _build_tavily(config: dict) -> BaseSearchEngine:
    from .tavily_engine import TavilySearchEngine

    c = _group(config, "tavily")
    return TavilySearchEngine(
        _resolve_secret(c.get("api_key", "")),
        search_depth=c.get("search_depth", "basic"),
    )


def _build_jina(config: dict) -> BaseSearchEngine:
    from .jina_engine import JinaSearchEngine

    c = _group(config, "jina")
    return JinaSearchEngine(
        api_key=_resolve_secret(c.get("api_key", "")),
        no_cache=bool(c.get("no_cache", False)),
    )


_BUILDERS: dict[str, Callable[[dict], BaseSearchEngine]] = {
    "ddgs": _build_ddgs,
    "exa": _build_exa,
    "tavily": _build_tavily,
    "jina": _build_jina,
}


def build_engine(config: dict) -> BaseSearchEngine:
    """Construct the engine named by ``config["engine"]`` (default ``ddgs``).

    ``config`` is the raw extension settings dict. Falls back to the DDG engine
    if the configured engine is unknown or fails to initialize (e.g. a missing
    API key), so web search always works.
    """
    from .ddgs_engine import DDGSearchEngine

    name = str(config.get("engine") or "ddgs").lower()
    builder = _BUILDERS.get(name)
    if builder is None:
        return DDGSearchEngine()
    try:
        return builder(config)
    except Exception:
        return DDGSearchEngine()
