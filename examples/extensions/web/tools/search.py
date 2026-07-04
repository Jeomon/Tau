from __future__ import annotations

from typing import Any

from engines import BaseSearchEngine
from engines import SearchMode as _SearchMode
from pydantic import BaseModel, Field

from tau.tool.render import call_line
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


def _render_web_search_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("web_search", args.get("query", ""))


class _WebSearchSchema(BaseModel):
    query: str = Field(
        ...,
        description=(
            "The search query. Be specific — include names, versions, or error "
            "messages for better results. Supports site: filtering to restrict "
            "results to a domain, e.g. 'asyncio timeout site:docs.python.org'."
        ),
        examples=[
            "Python asyncio timeout handling",
            "TypeError: cannot unpack non-sequence NoneType",
            "fastapi dependency injection site:fastapi.tiangolo.com",
        ],
    )
    mode: _SearchMode = Field(
        default=_SearchMode.text,
        description="Search mode: 'text' (default), 'news', 'images', 'videos', or 'books'.",
        examples=["text", "news"],
    )
    max_results: int = Field(
        default=10,
        description=(
            "Number of results to return (default 10). "
            "Increase to 20+ for broader coverage."
        ),
        examples=[10, 20],
    )


_PREVIEW_RESULTS = 3


def _result_lines(r: dict, mode: _SearchMode) -> tuple[str, str]:
    """Return (title, url) for a normalized result."""
    title = r.get("title", "")
    match mode:
        case _SearchMode.news:
            src = r.get("source", "")
            date = r.get("date", "")
            title += (f"  [{src}]" if src else "") + (f"  {date}" if date else "")
        case _SearchMode.videos:
            dur = r.get("duration", "")
            title += f"  [{dur}]" if dur else ""
        case _SearchMode.books:
            author = r.get("author", "")
            title += f" — {author}" if author else ""
    return title, r.get("url", "")


def _render_web_search(content: str, opts: Any) -> list[str]:
    # Style via the theme on the render options (stable extension surface)
    # instead of importing ANSI codes from Tau internals.
    _id = lambda s: s  # noqa: E731 — fallback when no theme (e.g. outside the TUI)
    muted = getattr(opts.theme, "muted", _id)

    metadata = opts.metadata or {}
    # query = metadata.get("query", "")  # Unused variable
    mode = metadata.get("mode", "text")
    result_count = metadata.get("result_count", 0)
    results = metadata.get("results", [])

    mode_tag = f"  {muted(mode)}" if mode != "text" else ""
    result_word = "result" if result_count == 1 else "results"
    summary = f"Found {result_count} {result_word}{mode_tag}"

    if not results:
        return [summary]

    out = [summary]
    for i, r in enumerate(results, 1):
        title, url = _result_lines(r, _SearchMode(mode))
        out.append(f"{i}  {title}")
        if url:
            out.append(f"   {muted(url)}")
    return out


def _format_results(mode: _SearchMode, query: str, results: list[dict]) -> str:
    lines = [f"Search results ({mode}) for: {query}"]
    for idx, r in enumerate(results, start=1):
        title, url = _result_lines(r, mode)
        lines.append(f"{idx}. {title}")
        if mode is _SearchMode.images and r.get("image"):
            lines.append(f"   Image: {r['image']}")
            lines.append(f"   Source: {url}")
        else:
            lines.append(f"   URL: {url}")
        if mode is _SearchMode.books:
            extra = f"{r.get('publisher', '')}  {r.get('info', '')}".strip()
            if extra:
                lines.append(f"   {extra}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return "\n".join(lines)


class WebSearchTool(Tool):
    def __init__(self, engine: BaseSearchEngine) -> None:
        self._engine = engine
        super().__init__(
            name="web_search",
            description=(
                "Search the web and return results. Supports multiple modes: "
                "'text' for pages, 'news' for articles, 'images' for pictures, "
                "'videos' for video content, 'books' for books. "
                "Follow up with web_fetch to read the full content of a result."
            ),
            schema=_WebSearchSchema,
            kind=ToolKind.Web,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_web_search,
            render_call=_render_web_search_call,
            render_shell="default",
            prompt_guidelines=(
                "Use for current information not in the codebase. "
                "Follow up with web_fetch to read a full page from the results."
            ),
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        query = invocation.params.get("query")
        if not query:
            return ToolResult.error(invocation.id, "Parameter 'query' is required.")

        mode = _SearchMode(invocation.params.get("mode", _SearchMode.text))
        max_results = invocation.params.get("max_results", 10)

        if not self._engine.supports(mode):
            modes_list = ', '.join(sorted(m.value for m in self._engine.supported_modes))
            return ToolResult.error(
                invocation.id,
                f"The '{self._engine.name}' engine does not support '{mode}' mode. "
                f"Supported modes: {modes_list}.",
            )

        try:
            results = await self._engine.search(query, mode, max_results)
        except Exception as e:
            return ToolResult.error(invocation.id, f"Search failed: {e}")

        metadata = {
            "query": query,
            "mode": str(mode),
            "result_count": len(results),
            "max_results": max_results,
            "results": results,
            "engine": self._engine.name,
        }

        if not results:
            return ToolResult.ok(invocation.id, f"No results found for: {query}", metadata=metadata)

        return ToolResult.ok(
            invocation.id, _format_results(mode, query, results), metadata=metadata
        )
