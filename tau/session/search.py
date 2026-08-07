"""Find a past session by what was said in it.

`/resume` lists sessions by name and date, which only helps when you remember
one of those. The thing you usually remember is the content — "the session where
we fixed the compaction race" — and nothing could answer that.

Matching is a case-insensitive substring test, deliberately: it needs no index,
no schema change, and no query syntax to learn. The cost is kept down by testing
the raw line *before* parsing it — only lines that already contain the query are
turned into entries — so a miss costs a `str.find` per line rather than a JSON
parse.

Only the JSONL backend is searched. `SQLiteSessionStorage` exists but nothing
in production constructs it — `SessionManager` builds `InMemorySessionStorage`
or `FileSessionStorage` and no setting selects otherwise — so a SQLite search
path would be code against a backend no user can reach. Sessions whose path is
not a `.jsonl` file are skipped explicitly rather than scanned as text, so if
that backend is ever wired up this returns nothing loudly (a warning) instead
of nothing silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau.session.types import SessionInfo

_log = logging.getLogger(__name__)

#: Characters of surrounding text kept either side of a match.
SNIPPET_RADIUS = 60


@dataclass
class SearchHit:
    """One matching entry, with enough context to decide whether it is the one."""

    session: SessionInfo
    entry_id: str
    timestamp: float
    role: str
    #: The matched text with a little either side, whitespace collapsed.
    snippet: str


def _snippet(text: str, needle_lower: str) -> str:
    """The match plus a little context, on one line."""
    collapsed = " ".join(text.split())
    position = collapsed.lower().find(needle_lower)
    if position < 0:
        return collapsed[: SNIPPET_RADIUS * 2].strip()
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(collapsed), position + len(needle_lower) + SNIPPET_RADIUS)
    prefix = "\u2026" if start > 0 else ""
    suffix = "\u2026" if end < len(collapsed) else ""
    return prefix + collapsed[start:end].strip() + suffix


def _entry_text(payload: dict[str, Any]) -> tuple[str, str]:
    """Extract (role, searchable text) from a raw entry dict.

    Works on the parsed dict rather than a validated model: search must not
    fail on an entry shape it does not recognise, and most of what is worth
    searching is plain text on a message's content blocks.
    """
    message = payload.get("message")
    if isinstance(message, dict):
        role = str(message.get("role", "") or "")
        contents = message.get("contents")
        if isinstance(contents, list):
            parts = [
                block.get("content", "")
                for block in contents
                if isinstance(block, dict) and isinstance(block.get("content"), str)
            ]
            return role, "\n".join(p for p in parts if p)
        return role, ""
    # Compaction and branch summaries are prose too, and often the most
    # memorable thing in a long session.
    summary = payload.get("summary")
    if isinstance(summary, str):
        return str(payload.get("type", "summary")), summary
    return "", ""


def _search_jsonl(file: Path, needle_lower: str, info: SessionInfo, limit: int) -> list[SearchHit]:
    """Scan one JSONL session, parsing only the lines that already match."""
    from pydantic_core import from_json

    hits: list[SearchHit] = []
    if file.suffix != ".jsonl":
        # A SQLite session's path is the database file. Scanning it as text
        # would find nothing and report that as "no match", which is a lie.
        _log.warning("cannot search %s: only JSONL sessions are searchable", file)
        return hits
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if needle_lower not in line.lower():
                    continue  # the cheap test: no parse, no allocation
                try:
                    payload = from_json(line)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                role, text = _entry_text(payload)
                if needle_lower not in text.lower():
                    continue  # matched metadata (an id, a path), not content
                hits.append(
                    SearchHit(
                        session=info,
                        entry_id=str(payload.get("id", "")),
                        timestamp=float(payload.get("timestamp", 0.0) or 0.0),
                        role=role,
                        snippet=_snippet(text, needle_lower),
                    )
                )
                if len(hits) >= limit:
                    break
    except OSError:
        _log.debug("could not read %s while searching", file, exc_info=True)
    return hits


def search_sessions(
    query: str,
    *,
    sessions: list[SessionInfo] | None = None,
    cwd: Path | str | None = None,
    limit_per_session: int = 3,
    limit: int = 50,
) -> list[SearchHit]:
    """Find entries containing ``query`` across sessions, newest session first.

    ``sessions`` restricts the search to an already-listed set; without it the
    current project's sessions are searched, or every project's when ``cwd`` is
    also omitted.
    """
    needle = query.strip()
    if not needle:
        return []
    needle_lower = needle.lower()

    if sessions is None:
        from tau.session.manager import SessionManager

        sessions = SessionManager.list_all() if cwd is None else SessionManager.list(cwd)

    hits: list[SearchHit] = []
    for info in sessions:
        if len(hits) >= limit:
            break
        remaining = min(limit_per_session, limit - len(hits))
        hits.extend(_search_jsonl(info.path, needle_lower, info, remaining))
    return hits
