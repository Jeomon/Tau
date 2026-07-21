"""web — Web search and fetch tools with a pluggable engine backend.

Provides two tools:
  web_search  Search the web (text, news, images, videos, books).
  web_fetch   Fetch a URL and return its content as text, with optional
              LLM-guided extraction to pull out only the relevant parts.

The active engine is selected via settings.json; each engine has its own group
of settings (default: DuckDuckGo):

  {
    "extensions": {
      "list": [{
        "path": ".tau/extensions/web",
        "settings": {
          "engine": "ddgs",                       // "ddgs" | "exa" | "tavily" | "jina"
          "ddgs":   { "region": "us-en", "safesearch": "off" },
          "exa":    { "api_key": "$EXA_API_KEY", "type": "auto" },
          "tavily": { "api_key": "$TAVILY_API_KEY", "search_depth": "basic" },
          "jina":   { "api_key": "$JINA_API_KEY", "no_cache": false }
        }
      }]
    }
  }

api_key accepts a literal value, "$ENV_VAR", or "!shell-command".

The /settings panel — including the per-engine sub-panels — is generated
automatically from the ``settings`` schema in manifest.json. Changing a value
persists it and reloads this extension, so it takes effect live.

Requires: ddgs (default engine) — declared in manifest.json. The exa / tavily
engines additionally need the `exa-py` / `tavily-python` packages installed.
"""

from __future__ import annotations

from .engines import build_engine
from .tools.fetch import WebFetchTool
from .tools.search import WebSearchTool


def register(tau) -> None:
    config = tau.config or {}
    # The settings panel is auto-attached from manifest.json regardless, so the
    # tools can be re-enabled from /settings even after being turned off here.
    if not config.get("enabled", True):
        return
    engine = build_engine(config)
    tau.register_tool(WebSearchTool(engine))
    tau.register_tool(WebFetchTool(engine))

    @tau.on("extension_unload")
    async def _close_engine(_event, _ctx) -> None:
        await engine.aclose()
