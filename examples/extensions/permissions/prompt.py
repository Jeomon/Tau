"""Asking the user, across every surface Tau can run on.

``ui.select`` is used rather than a bespoke component because it is the one
dialog that exists identically in the TUI *and* over RPC, so a single code path
covers both. The TUI renders a picker; an RPC client gets a ``select`` request.

Timeouts are enforced here with :func:`asyncio.wait_for` rather than by passing
a ``timeout=`` through, because the interactive and RPC ``select`` signatures
differ. It also makes the important guarantee unconditional: **expiry denies.**
A prompt that times out must never be mistaken for consent.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from .paths import extract_path
from .preview import build, content_summary, proposed_edit_lines, read_lines
from .rules import Decision

_log = logging.getLogger(__name__)

Outcome = Literal["allow_once", "allow_session", "deny"]

_ALLOW_ONCE = "Allow Once"
_DENY = "Deny"


#: Long commands are truncated in the detail block. Approving still applies to
#: the whole thing — the block says so explicitly rather than implying that
#: what is shown is all that runs.
_MAX_SHOWN = 240


def _clip(text: str, limit: int = _MAX_SHOWN) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def headline(decision: Decision, params: dict | None = None) -> str:
    """Single line for the picker title.

    Deliberately short: ``UIContext.select`` renders the title as the *first
    option's* right-hand column, so anything long or multi-line lands beside
    "Allow once" and reads as if it belonged to that choice. The specifics go
    in the detail block instead.
    """
    tool = _tool_of(decision, params or {})
    if tool == "edit":
        return "Approve this edit?"
    if tool == "write":
        return "Approve writing this file?"
    return {
        "command": "Approve this terminal command?",
        "external_directory": "Approve access outside the project?",
        "path": "Approve access to this file?",
    }.get(decision.surface, "Approve this tool call?")


def detail_lines(
    decision: Decision,
    params: dict | None = None,
    cwd: Path | None = None,
    theme: Any = None,
    registry: Any = None,
) -> list[str]:
    """The block shown inside the picker, as aligned ``label  value`` rows.

    ``theme`` is the live ``LayoutTheme``. Given one, the diff is coloured
    with the same styles tool results use, so a write/edit prompt reads the
    way the resulting diff will. Without one the block stays plain text,
    which is what the RPC path and the tests want.
    """
    params = params or {}
    rows: list[tuple[str, str]] = []
    raw_path: str | None = None

    if decision.surface == "command":
        full = params.get("cmd")
        full = _clip(full) if isinstance(full, str) else decision.target
        rows.append(("command", full))
        # The gate decides on the most restrictive segment, but approval
        # releases the entire string — naming only the segment would overstate
        # what is being agreed to. Only worth a row when the segment is *not*
        # already visible in the command above: a target that is a substring of
        # what was just printed (`pytest -q` inside `pytest -q | tail`) repeats
        # the command row and pushes the choices further down the screen.
        if decision.target and decision.target not in full:
            rows.append(("segment", _clip(decision.target)))
    elif decision.surface in ("path", "external_directory"):
        rows.append(("path", _clip(decision.target)))
        raw_path = decision.target
    else:
        rows.append(("tool", decision.target))
        # A tool-level decision still acts on something; naming the tool alone
        # would ask "approve write?" without saying which file.
        target, _ = extract_path(decision.target, params)
        if target:
            rows.append(("path", _clip(target)))
            raw_path = target

    if decision.command_context:
        # `rm` inside `$(…)` reads very differently from `rm` typed directly.
        rows.append(("context", decision.command_context.replace("_", " ")))
    # A catch-all match and a rule that just restates the tool name carry no
    # information; showing them trains people to skim past the rule row.
    if (
        decision.matched_pattern
        and decision.matched_pattern not in ("*", "**")
        and decision.matched_pattern != decision.target
    ):
        rows.append(("rule", decision.matched_pattern))
    if decision.reason:
        rows.append(("reason", _clip(decision.reason)))

    tool = _tool_of(decision, params)
    if tool in ("write", "edit"):
        summary = content_summary(params)
        if summary:
            rows.append(("writes", summary))

    # No block header and no closing note: the picker's own heading already
    # says what is being approved, and both lines only pushed the choices
    # further down the prompt people see most often. What approval actually
    # covers is still legible from the rows — `command` carries the whole
    # string, and `segment` names the gated part whenever the clip hides it.
    # Unindented: the picker frames every one of its own rows with two
    # columns already, so indenting here again pushed the detail two columns
    # deeper than the question and the choices for no hierarchy it was
    # expressing.
    width = max(len(label) for label, _ in rows)
    lines = [f"{label.ljust(width)}   {value}" for label, value in rows]

    # The content is the decision for write/edit — a path alone tells you
    # nothing about whether the change is acceptable. The diff sits flush with
    # everything else: its own -/+ column already marks it as a diff, so an
    # extra indent bought nothing and cost two columns of a line that is
    # usually the longest in the block.
    if tool in ("write", "edit") and raw_path and cwd is not None:
        label, diff = build(tool, raw_path, cwd, params)
        native = _render_like_the_tool(tool, raw_path, cwd, params, registry, theme)
        if native:
            lines += ["", *native]
        elif diff:
            lines += ["", f"{label}:", *_colorize(diff, theme)]

    return lines


def _render_like_the_tool(
    tool: str,
    raw_path: str,
    cwd: Path,
    params: dict,
    registry: Any,
    theme: Any,
) -> list[str]:
    """Preview the change with the tool's *own* renderer, or ``[]`` if it can't.

    Approving a diff that is formatted one way and then reading the result
    formatted another way makes the two hard to compare — and the difference
    is not cosmetic here: the tool renders hashline anchors, which are what a
    later edit has to reference. Calling ``Tool.render_result`` means the
    preview *is* the result view, and stays that way if the tool's renderer
    changes.

    Falls back to the plain unified diff whenever anything is unavailable
    (no registry outside the TUI, a tool without a renderer, a renderer that
    raises): the block is worth showing in some form, and none of this is
    worth failing a security prompt over.
    """
    if registry is None or theme is None:
        return []
    try:
        from tau.tool.types import ToolRenderOptions

        entry = registry.get(tool)
        render = getattr(entry, "render_result", None)
        if not callable(render):
            return []

        metadata = _result_metadata(tool, raw_path, cwd, params)
        if metadata is None:
            return []
        # expanded=True: a prompt has no ctrl+o, so a collapsed hunk would
        # hide exactly the lines the decision rests on.
        opts = ToolRenderOptions(metadata=metadata, theme=theme, expanded=True)
        rendered: Any = render(metadata.pop("_summary", ""), opts) or []
        return [str(line) for line in rendered]
    except Exception:  # noqa: BLE001 - never fail the prompt over its own preview
        _log.debug("permissions: could not render with the tool's renderer", exc_info=True)
        return []


def _result_metadata(tool: str, raw_path: str, cwd: Path, params: dict) -> dict | None:
    """Build the metadata the tool's renderer expects, before the tool has run."""
    import difflib

    path = Path(raw_path)
    if not path.is_absolute():
        path = cwd / path

    existing = read_lines(path)
    if tool == "edit":
        new_lines = proposed_edit_lines(existing, params)
    else:
        content = params.get("content")
        if not isinstance(content, str):
            return None
        new_lines = content.splitlines()
    if new_lines is None:
        return None

    old = existing or []
    diff = "\n".join(difflib.unified_diff(old, new_lines, lineterm="", n=3))
    if not diff:
        return None
    return {
        "_summary": f"Editing {raw_path}",
        "file_path": str(path),
        "diff": diff,
        "lines_added": sum(1 for ln in diff.splitlines() if ln.startswith("+") and ln[1:2] != "+"),
        "lines_removed": sum(
            1 for ln in diff.splitlines() if ln.startswith("-") and ln[1:2] != "-"
        ),
    }


def _colorize(diff: list[str], theme: Any) -> list[str]:
    """Colour a plain unified diff with the theme's styles — the fallback path.

    Used only when the tool's own renderer is unavailable. Reuses
    ``render_diff``, which is what tool results go through, so even the
    fallback shares a palette with the transcript.
    """
    if theme is None:
        return diff
    try:
        from tau.tui.style import apply_style
        from tau.tui.utils import render_diff

        m = theme.message
        return render_diff(
            "\n".join(diff),
            added=lambda s: apply_style(m.diff_added, s),
            removed=lambda s: apply_style(m.diff_removed, s),
            context=lambda s: apply_style(m.diff_context, s),
            hunk=lambda s: apply_style(m.diff_hunk, s),
            inverse=m.diff_inverse,
        )
    except Exception:  # noqa: BLE001 - a colour lookup must never cost the prompt
        _log.debug("permissions: could not colour the diff", exc_info=True)
        return diff


def _tool_of(decision: Decision, params: dict) -> str | None:
    """Which tool this decision is about, when that is knowable.

    A `tool` decision names it directly. A `path` decision does not, so infer
    it from the parameter shape — only write/edit carry content.
    """
    if decision.surface == "tool":
        return decision.target
    if "new_content" in params:
        return "edit"
    if "content" in params:
        return "write"
    return None


async def ask(
    ui: Any,
    decision: Decision,
    *,
    timeout_seconds: int,
    suggestion: str | None = None,
    params: dict | None = None,
    cwd: Path | None = None,
    registry: Any = None,
) -> tuple[Outcome, str | None]:
    """Prompt for one decision.

    Everything the decision needs goes *inside* the picker, as a multi-line
    title: the question, then the specifics under it.

    The layout draws a selector between the editor's two dividers, and that
    frame is the whole prompt. Anything mounted elsewhere lands outside it —
    a ``notify`` appends to the message list, so it also survives the choice
    and leaves a permanent duplicate of the tool-call block that appears the
    moment the gate resolves; a widget renders above the top divider. Neither
    reads as part of the question being asked.

    Surfaces with no components (RPC) get the short headline plus a
    ``notify``: there is no picker frame to put anything in, and a client
    rendering the title on one line would truncate the block to nothing.

    Returns the outcome and, for ``allow_session``, the pattern to remember.
    Any failure to obtain an answer — cancel, timeout, missing UI, a raising
    dialog — resolves to ``deny``.
    """
    if ui is None:
        return "deny", None

    session_label = (
        f"Allow for this session ({suggestion})" if suggestion else "Allow for this session"
    )
    options = [_ALLOW_ONCE, session_label, _DENY]

    title = headline(decision, params)
    try:
        # Only the component surface can render colour, and only it can reuse a
        # tool's renderer; the RPC path takes neither and keeps the block plain.
        components = bool(getattr(ui, "supports_components", False))
        detail = detail_lines(
            decision,
            params,
            cwd,
            getattr(ui, "theme", None) if components else None,
            registry if components else None,
        )
        if components:
            title = "\n".join([title, "", *detail])
        else:
            ui.notify(detail, "warning")
    except Exception:  # noqa: BLE001 - the picker still works without the block
        _log.debug("permissions: could not render the detail block", exc_info=True)

    try:
        chooser = ui.select(title, options)
        choice = (
            await asyncio.wait_for(chooser, timeout_seconds)
            if timeout_seconds > 0
            else await chooser
        )
    except TimeoutError:
        _log.info("permissions: prompt timed out for %s; denying", decision.target)
        return "deny", None
    except Exception:  # noqa: BLE001 - a broken dialog must not grant access
        _log.exception("permissions: prompt failed for %s; denying", decision.target)
        return "deny", None

    if choice == _ALLOW_ONCE:
        return "allow_once", None
    if choice == session_label:
        return "allow_session", suggestion or decision.target
    # Explicit deny, or the user dismissed the dialog.
    return "deny", None
