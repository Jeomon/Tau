from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def normalize(obj: Any, cwd: str) -> Any:
    """Recursively fix raw LSP output for LLM / TUI consumption:
    - file:// URIs → relative paths (key renamed from 'uri' to 'path')
    - line/character integers → 1-based (LSP protocol is 0-based)
    """
    if isinstance(obj, list):
        return [normalize(item, cwd) for item in obj]
    if not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k == "uri" and isinstance(v, str) and v.startswith("file://"):
            abs_path = urlparse(v).path
            try:
                out["path"] = str(Path(abs_path).relative_to(cwd))
            except ValueError:
                out["path"] = abs_path
        elif k in ("line", "character") and isinstance(v, int):
            out[k] = v + 1  # 0-based → 1-based
        else:
            out[k] = normalize(v, cwd)
    return out


def read_snippet(path_str: str, start_line: int, end_line: int, max_lines: int) -> str | None:
    """Read lines start_line..end_line from file (1-based). Returns None on any error."""
    try:
        lines = Path(path_str).read_text(errors="replace").splitlines()
        s = max(0, start_line - 1)  # back to 0-based for list indexing
        e = min(len(lines), start_line - 1 + max_lines, end_line)
        return "\n".join(lines[s:e]) or None
    except Exception:
        return None


def add_snippets(obj: Any, cwd: str, max_lines: int) -> Any:
    """Walk normalized LSP output and attach a 'snippet' field to any dict that has
    both 'path' and 'range', so the LLM sees code instead of bare coordinates."""
    if isinstance(obj, list):
        return [add_snippets(item, cwd, max_lines) for item in obj]
    if not isinstance(obj, dict):
        return obj

    # Recurse children first
    out = {k: add_snippets(v, cwd, max_lines) for k, v in obj.items()}

    path_str = out.get("path")
    range_ = out.get("range")
    if path_str and isinstance(range_, dict):
        start = range_.get("start", {})
        end_ = range_.get("end", {})
        if isinstance(start, dict) and "line" in start:
            full = str(Path(cwd) / path_str) if not Path(path_str).is_absolute() else path_str
            snippet = read_snippet(
                full,
                start["line"],
                end_.get("line", start["line"]),
                max_lines,
            )
            if snippet:
                out["snippet"] = snippet

    return out
