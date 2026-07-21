"""Standalone HTML export of a session transcript.

Produces a single self-contained file — no external CSS, JS, or fonts — so the
result can be attached to a ticket or opened straight from disk. Used by RPC
mode's ``export_html`` command.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

_STYLE = """
:root { color-scheme: light dark; }
body {
  margin: 0 auto; padding: 2rem 1.25rem; max-width: 52rem;
  font: 15px/1.6 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  color: #1c1c1e; background: #fff;
}
header { border-bottom: 1px solid #d8d8dc; padding-bottom: 1rem; margin-bottom: 1.5rem; }
h1 { font-size: 1.35rem; margin: 0 0 .5rem; }
dl.meta { display: grid; grid-template-columns: max-content 1fr; gap: .15rem .75rem; margin: 0; }
dl.meta dt { color: #6c6c70; }
dl.meta dd { margin: 0; font-variant-numeric: tabular-nums; }
section.msg {
  margin: 0 0 1.25rem; padding: .85rem 1rem;
  border-radius: .5rem; border: 1px solid #e3e3e7;
}
section.msg > .role {
  font-size: .75rem; text-transform: uppercase; letter-spacing: .06em;
  color: #6c6c70; margin-bottom: .4rem;
}
section.user { background: #f4f6fb; }
section.assistant { background: #fff; }
section.terminal { background: #f7f7f8; }
section.custom { background: #fbf8f2; }
pre {
  margin: .35rem 0 0; padding: .6rem .7rem; overflow-x: auto;
  background: #f2f2f4; border-radius: .35rem;
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap; word-break: break-word;
}
.thinking { color: #6c6c70; font-style: italic; }
.tool { border-left: 3px solid #b8b8bd; padding-left: .7rem; margin-top: .6rem; }
.tool .name { font-weight: 600; font-size: .85rem; }
.error { color: #b3261e; }
.media { color: #6c6c70; font-size: .85rem; margin-top: .4rem; }
footer { margin-top: 2rem; color: #6c6c70; font-size: .8rem; }
@media (prefers-color-scheme: dark) {
  body { color: #e8e8ea; background: #131316; }
  header { border-color: #33333a; }
  dl.meta dt, .thinking, .media, footer, section.msg > .role { color: #9a9aa0; }
  section.msg { border-color: #33333a; }
  section.user { background: #1a1d24; }
  section.assistant { background: #17171b; }
  section.terminal, section.custom { background: #1b1b1f; }
  pre { background: #202027; }
  .error { color: #f2b8b5; }
  .tool { border-color: #4a4a52; }
}
"""

# Keys are Role *values*, which do not always match the enum member name —
# Role.BASH_EXECUTION is "terminal_execution".
_ROLE_LABELS = {
    "user": "User",
    "assistant": "Assistant",
    "terminal_execution": "Terminal",
    "custom": "Note",
    "system": "System",
    "compaction_summary": "Compaction summary",
    "branch_summary": "Branch summary",
    "skill_invocation": "Skill",
    "template_invocation": "Prompt template",
}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _pre(text: str, css_class: str = "") -> str:
    cls = f' class="{css_class}"' if css_class else ""
    return f"<pre{cls}>{_esc(text)}</pre>"


def _render_contents(contents: list[Any]) -> list[str]:
    """Render one message's content blocks."""
    out: list[str] = []
    for content in contents or []:
        kind = getattr(content, "type", "")
        if kind == "text":
            text = getattr(content, "content", "")
            if text.strip():
                out.append(_pre(text))
        elif kind == "thinking":
            text = getattr(content, "content", "")
            if text.strip():
                out.append(_pre(text, "thinking"))
        elif kind == "lines":
            lines = getattr(content, "lines", []) or []
            if lines:
                out.append(_pre("\n".join(str(line) for line in lines)))
        elif kind == "tool_call":
            args = getattr(content, "args", {}) or {}
            try:
                rendered = json.dumps(args, indent=2, default=str)
            except Exception:
                rendered = str(args)
            out.append(
                '<div class="tool"><div class="name">'
                f"call {_esc(getattr(content, 'name', ''))}</div>{_pre(rendered)}</div>"
            )
        elif kind == "tool_result":
            css = "error" if getattr(content, "is_error", False) else ""
            out.append(
                '<div class="tool"><div class="name">'
                f"result {_esc(getattr(content, 'tool_name', ''))}</div>"
                f"{_pre(getattr(content, 'content', ''), css)}</div>"
            )
        elif kind in ("image", "audio", "video", "file"):
            # Media bytes are deliberately not inlined — the export stays small
            # and shareable rather than embedding megabytes of base64.
            out.append(f'<div class="media">[{_esc(kind)} attachment omitted]</div>')
    return out


def _render_message(message: Any) -> str:
    """Render one session message as an HTML section."""
    role = str(getattr(getattr(message, "role", ""), "value", getattr(message, "role", "")))
    label = _ROLE_LABELS.get(role, role or "Message")
    body: list[str] = []

    command = getattr(message, "command", None)
    if command is not None:  # TerminalExecutionMessage
        exit_code = getattr(message, "exit_code", None)
        suffix = ""
        if getattr(message, "cancelled", False):
            suffix = " (cancelled)"
        elif exit_code:
            suffix = f" (exit {exit_code})"
        body.append(_pre(f"$ {command}{suffix}"))
        output = getattr(message, "output", "")
        if output:
            body.append(_pre(output))
        css = "terminal"
    else:
        body.extend(_render_contents(getattr(message, "contents", [])))
        error = getattr(message, "error", "")
        if error:
            body.append(_pre(error, "error"))
        css = role if role in ("user", "assistant") else "custom"

    if not body:
        return ""
    return (
        f'<section class="msg {css}"><div class="role">{_esc(label)}</div>'
        + "".join(body)
        + "</section>"
    )


def session_to_html(session_manager: Any, *, title: str | None = None) -> str:
    """Render the session's current branch as a standalone HTML document."""
    from tau.session.types import MessageEntry

    name = None
    get_name = getattr(session_manager, "get_session_name", None)
    if callable(get_name):
        name = get_name()
    session_id = getattr(session_manager, "session_id", None)
    heading = title or name or f"Session {session_id or ''}".strip()

    sections = [
        rendered
        for entry in session_manager.get_branch()
        if isinstance(entry, MessageEntry) and (rendered := _render_message(entry.message))
    ]

    meta = {
        "Session": session_id or "—",
        "Name": name or "—",
        "Directory": str(getattr(session_manager, "cwd", "") or "—"),
        "Messages": len(sections),
        "Exported": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    meta_html = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in meta.items())

    return (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(heading)}</title><style>{_STYLE}</style></head><body>"
        f"<header><h1>{_esc(heading)}</h1><dl class=\"meta\">{meta_html}</dl></header>"
        + ("".join(sections) or "<p>This session has no messages.</p>")
        + "<footer>Exported by tau</footer></body></html>\n"
    )


def export_session_html(session_manager: Any, output_path: str | Path) -> Path:
    """Write the session transcript to ``output_path`` and return the path."""
    path = Path(output_path).expanduser()
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_to_html(session_manager), encoding="utf-8")
    return path
