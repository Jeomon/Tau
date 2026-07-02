from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from tau.tool.render import call_line
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

_PAGE_SIZE = 5
_BROWSE_LIMIT = 25
_SNIPPET_RADIUS = 70


# ── Entry rendering ────────────────────────────────────────────────────────────


@dataclass
class Record:
    index: int
    role: str
    text: str


def _render_message(msg: Any, T: dict[str, Any]) -> tuple[str, str]:
    """Return ``(role_label, full_text)`` for a tau AgentMessage."""
    if isinstance(msg, T["UserMessage"]):
        text = "\n".join(c.content for c in msg.contents if isinstance(c, T["TextContent"]))
        return "user", text
    if isinstance(msg, T["AssistantMessage"]):
        parts: list[str] = []
        for c in msg.contents:
            if isinstance(c, (T["TextContent"], T["ThinkingContent"])):
                parts.append(c.content)
            elif isinstance(c, T["ToolCallContent"]):
                args = ", ".join(f"{k}={v!r}" for k, v in (c.args or {}).items())
                parts.append(f"{c.name}({args})")
        return "assistant", "\n".join(p for p in parts if p)
    if isinstance(msg, T["ToolMessage"]):
        parts = [
            c.content
            for c in msg.contents
            if isinstance(c, T["ToolResultContent"]) and c.content
        ]
        return "tool", "\n".join(parts)
    if isinstance(msg, T["TerminalExecutionMessage"]):
        body = f"$ {msg.command}"
        if msg.output:
            body += f"\n{msg.output}"
        return "terminal", body
    return "", ""


def render_entries(entries: list[Any]) -> list[Record]:
    """Flatten session entries into searchable records with stable indices."""
    from tau.message.types import (
        AssistantMessage,
        TerminalExecutionMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
    )
    from tau.session.types import (
        BranchSummaryEntry,
        CompactionEntry,
        CustomMessageEntry,
        MessageEntry,
    )

    T = {
        "AssistantMessage": AssistantMessage,
        "TerminalExecutionMessage": TerminalExecutionMessage,
        "TextContent": TextContent,
        "ThinkingContent": ThinkingContent,
        "ToolCallContent": ToolCallContent,
        "ToolMessage": ToolMessage,
        "ToolResultContent": ToolResultContent,
        "UserMessage": UserMessage,
    }

    records: list[Record] = []
    for entry in entries:
        role, text = "", ""
        if isinstance(entry, MessageEntry):
            role, text = _render_message(entry.message, T)
        elif isinstance(entry, CompactionEntry):
            role, text = "compaction", entry.summary
        elif isinstance(entry, BranchSummaryEntry):
            role, text = "branch_summary", entry.summary
        elif isinstance(entry, CustomMessageEntry):
            role = f"custom:{entry.custom_type}"
            text = "\n".join(
                getattr(c, "content", "") for c in entry.content if isinstance(c, TextContent)
            )
        if not text.strip():
            continue
        records.append(Record(index=len(records), role=role, text=text))
    return records


# ── Search ─────────────────────────────────────────────────────────────────────


def _compile_terms(query: str) -> list[re.Pattern[str]]:
    terms: list[re.Pattern[str]] = []
    for raw in query.split():
        try:
            terms.append(re.compile(raw, re.I))
        except re.error:
            terms.append(re.compile(re.escape(raw), re.I))
    return terms


def _search(records: list[Record], query: str, page: int) -> str:
    terms = _compile_terms(query)
    if not terms:
        return _browse(records)

    n = len(records) or 1
    # Document frequency per term → rare terms weigh more (IDF-style).
    df = [sum(1 for r in records if t.search(r.text)) for t in terms]
    weights = [math.log((n + 1) / (d + 1)) + 1.0 for d in df]

    scored: list[tuple[float, Record, list[str]]] = []
    for r in records:
        score = 0.0
        matched: list[str] = []
        for t, w in zip(terms, weights, strict=False):
            m = t.search(r.text)
            if m:
                score += w
                matched.append(m.group(0))
        if score > 0:
            scored.append((score, r, matched))

    if not scored:
        return f"No matches for {query!r} in {n} entries."

    scored.sort(key=lambda s: (-s[0], s[1].index))
    total = len(scored)
    pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = max(1, min(page, pages))
    start = (page - 1) * _PAGE_SIZE
    window = scored[start : start + _PAGE_SIZE]

    lines = [f"{total} match(es) for {query!r} — page {page}/{pages}"]
    for score, r, matched in window:
        snip = _snippet(r.text, matched[0]) if matched else r.text[: _SNIPPET_RADIUS * 2]
        lines.append(f"\n[{r.index}] ({r.role})  score={score:.1f}")
        lines.append(f"    {snip}")
    lines.append(
        f"\nExpand full content with vcc_recall(expand=[{window[0][1].index}, ...])."
    )
    return "\n".join(lines)


def _snippet(text: str, term: str, radius: int = _SNIPPET_RADIUS) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    idx = flat.lower().find(term.lower())
    if idx == -1:
        return flat[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(flat), idx + len(term) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


def _browse(records: list[Record]) -> str:
    if not records:
        return "No history entries found."
    recent = records[-_BROWSE_LIMIT:]
    lines = [f"Last {len(recent)} of {len(records)} entries:"]
    for r in recent:
        flat = re.sub(r"\s+", " ", r.text).strip()
        lines.append(f"[{r.index}] ({r.role}) {flat[:120]}")
    return "\n".join(lines)


def _expand(records: list[Record], indices: list[int]) -> str:
    by_index = {r.index: r for r in records}
    out: list[str] = []
    for i in indices:
        r = by_index.get(i)
        if r is None:
            out.append(f"[{i}] (not found)")
            continue
        out.append(f"[{r.index}] ({r.role})\n{r.text}")
    return "\n\n".join(out) if out else "No indices given."


def run_recall(
    entries: list[Any],
    *,
    query: str | None = None,
    page: int = 1,
    expand: list[int] | None = None,
) -> str:
    records = render_entries(entries)
    if expand:
        return _expand(records, expand)
    if query and query.strip():
        return _search(records, query.strip(), page)
    return _browse(records)


# ── Tool ───────────────────────────────────────────────────────────────────────


class VccRecallParams(BaseModel):
    query: str | None = Field(
        default=None,
        description="Search terms (whitespace-separated, each treated as a regex, OR-ranked). "
        "Omit to browse the most recent entries.",
    )
    page: int = Field(default=1, description="Result page (5 results per page).")
    expand: list[int] | None = Field(
        default=None,
        description="Entry indices (from a prior search) to return in full, untruncated.",
    )
    scope: str = Field(
        default="active",
        description="'active' searches the current conversation lineage; 'all' searches every "
        "branch in the session file.",
    )


def _entries_for_scope(runtime: Any, scope: str) -> list[Any]:
    sm = getattr(runtime, "session_manager", None)
    if sm is None:
        return []
    if scope == "all":
        return sm.get_entries()
    return sm.get_branch()


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    if args.get("expand"):
        return call_line("vcc_recall", f"expand {args['expand']}")
    return call_line("vcc_recall", args.get("query") or "(browse)")


class VccRecallTool(Tool):
    """Lossless history search over the raw session — survives compaction."""

    def __init__(self, runtime_ref: Any) -> None:
        self._runtime_ref = runtime_ref
        super().__init__(
            name="vcc_recall",
            description=(
                "Search prior conversation history that was compacted away. Reads the raw "
                "session log, so work and decisions from before a compaction stay retrievable. "
                "Provide `query` to search (whitespace-separated terms, each a regex, OR-ranked), "
                "omit it to browse recent entries, or pass `expand` with entry indices from a "
                "prior search to get their full content. Use scope='all' to search every branch."
            ),
            schema=VccRecallParams,
            kind=ToolKind.Read,
            render_call=_render_call,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Any = None,
        signal: Any = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = VccRecallParams.model_validate(invocation.params)
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return ToolResult.error(invocation.id, "vcc_recall unavailable: runtime not ready")
        entries = _entries_for_scope(runtime, params.scope)
        content = run_recall(
            entries, query=params.query, page=params.page, expand=params.expand
        )
        return ToolResult.ok(invocation.id, content)
