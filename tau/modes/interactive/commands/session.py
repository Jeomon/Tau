from __future__ import annotations

import asyncio
from typing import Any

from tau.modes.interactive.commands.context import CommandContext
from tau.modes.interactive.ui_context import selector_future
from tau.tui.utils import strip_control_chars
from tau.utils.format import format_number

_SESSION_PAGE_SIZE = 20


async def open_resume_selector(ctx: CommandContext) -> None:
    from tau.session.manager import SessionManager

    sm = ctx.runtime.session_manager
    cwd = sm.cwd if sm is not None else None
    current_path = sm.session_file if sm is not None else None
    current_id = getattr(sm, "session_id", None) if sm is not None else None
    current_pager = None
    all_pager = None

    async def load_page(scope: str) -> None:
        nonlocal current_pager, all_pager
        try:
            if scope == "current":
                if current_pager is None:
                    current_pager = (
                        await asyncio.to_thread(SessionManager.pager, cwd) if cwd else None
                    )
                pager = current_pager
            else:
                if all_pager is None:
                    all_pager = await asyncio.to_thread(SessionManager.all_pager)
                pager = all_pager
            sessions, has_more = (
                await asyncio.to_thread(pager.next_page, _SESSION_PAGE_SIZE)
                if pager
                else ([], False)
            )
            total_count = pager.total_count if pager is not None else 0
        except Exception:
            sessions, has_more, total_count = [], False, 0
        ctx.layout.append_resume_sessions(scope, sessions, has_more, total_count)

    def load_more(scope: str) -> None:
        asyncio.create_task(load_page(scope))

    def commit(session: object) -> None:
        asyncio.ensure_future(_apply_resume(ctx, session))

    ctx.layout.open_resume_selector(
        sessions=[],
        loading=True,
        on_commit=commit,
        on_cancel=lambda: ctx.notify("Resume cancelled."),
        all_sessions_loader=lambda: [],
        on_load_all=lambda: load_more("all"),
        on_load_more=load_more,
        current_session_path=current_path,
        current_session_id=current_id,
    )
    load_more("current")


async def _apply_resume(ctx: CommandContext, target: object) -> None:
    """Resume the selected session.

    ``target`` is a SessionInfo when it comes from a picker, and a bare path
    when a caller only has one. The id matters for the SQLite backend, where
    one database holds every session of a project and the path cannot say
    which of them to open.
    """
    from pathlib import Path

    if target is None:
        return
    path = getattr(target, "path", target)
    session_id = getattr(target, "id", None)
    p = Path(str(path))
    try:
        await ctx.runtime.resume_session(p, session_id=session_id)
        label = session_id[:32] if session_id else p.stem[:32]
        ctx.notify(f"Resumed session {label}")
    except Exception as exc:
        ctx.notify(f"Failed to resume: {exc}")


def _message_snippet(message: object) -> tuple[str, str]:
    """Return (role_label, text_snippet) for any AgentMessage variant."""
    from tau.message.types import (
        BranchSummaryMessage,
        CompactionSummaryMessage,
        CustomMessage,
        SkillInvocationMessage,
        TemplateInvocationMessage,
        TerminalExecutionMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolResultContent,
    )

    role_attr = getattr(message, "role", "")
    role = getattr(role_attr, "value", role_attr) or type(message).__name__

    if isinstance(message, TerminalExecutionMessage):
        return "terminal", message.command
    if isinstance(message, CompactionSummaryMessage):
        return "compaction", message.summary
    if isinstance(message, BranchSummaryMessage):
        return "branch_summary", message.summary
    if isinstance(message, SkillInvocationMessage):
        return "skill", f"{message.name} {message.content}".strip()
    if isinstance(message, TemplateInvocationMessage):
        return "template", message.name

    contents = getattr(message, "contents", None)
    if isinstance(contents, list):
        parts: list[str] = []
        for c in contents:
            if isinstance(c, TextContent):
                parts.append(c.content)
            elif isinstance(c, ThinkingContent):
                parts.append(f"(thinking) {c.content}")
            elif isinstance(c, ToolCallContent):
                parts.append(f"[tool: {c.name}]")
            elif isinstance(c, ToolResultContent):
                prefix = "[error] " if c.is_error else ""
                parts.append(f"{prefix}{c.content}")
        text = " ".join(p for p in parts if p)
        if isinstance(message, CustomMessage):
            role = f"custom:{message.custom_type}"
        return role, text

    return role, ""


def _message_selectable(message: object) -> bool:
    """False for an assistant turn with unanswered tool calls
    (would create a dangling tool_call).
    """
    from tau.message.types import AssistantMessage

    return not (isinstance(message, AssistantMessage) and message.tool_calls())


def open_tree_selector(ctx: CommandContext) -> None:
    from tau.message.types import TextContent
    from tau.modes.interactive.components.tree_selector import TreeRow
    from tau.session.types import (
        BranchSummaryEntry,
        CompactionEntry,
        CustomInfoEntry,
        CustomMessageEntry,
        LabelEntry,
        MessageEntry,
        ModelChangeEntry,
        ThinkingLevelChangeEntry,
    )

    sm = ctx.runtime.session_manager
    if sm is None:
        ctx.notify("No active session.")
        return

    nodes = sm.get_tree()
    if not nodes:
        ctx.notify("Session tree is empty.")
        return

    current_leaf = sm.get_leaf_id()
    rows: list[TreeRow[str]] = []

    # Snippets were cut to a fixed 80 characters, so a wide terminal showed a
    # short column of text with the rest of the screen empty and words sliced
    # mid-token. The renderer already clips each row to the real width, so this
    # only has to keep a row from carrying an entire message: a slice of the
    # message text is held per entry, and a long session has thousands of them.
    # Budget the visible width plus enough slack that the renderer, not this
    # line, decides where the text ends — the prefix, role and label sit to the
    # left of it and vary per row.
    snippet_budget = max(80, ctx.tui.content_width)

    # Flatten once to map id -> parent_id, so we can walk current_leaf's
    # ancestor chain and mark the active path (independent of tree nesting).
    #
    # Taken straight from the entry list rather than by descending the tree.
    # The tree is built from exactly these entries, so the map is the same, and
    # a linear conversation nests one node deep per entry — recursing to build
    # it raised `RecursionError: maximum recursion depth exceeded` on any
    # session past roughly a thousand entries, which is what `/tree` reported
    # instead of opening. This is also what the comment above always described:
    # the map is flat, so building it has no reason to care how deep the tree is.
    parent_of: dict[str, str | None] = {entry.id: entry.parent_id for entry in sm.get_entries()}

    active_ids: set[str] = set()
    cur = current_leaf
    while cur is not None and cur in parent_of:
        active_ids.add(cur)
        cur = parent_of[cur]

    def _entry_role_text(entry: object) -> tuple[str, str] | None:
        if isinstance(entry, MessageEntry):
            return _message_snippet(entry.message)
        if isinstance(entry, CompactionEntry):
            return "compaction", entry.summary
        if isinstance(entry, BranchSummaryEntry):
            return "branch_summary", entry.summary
        if isinstance(entry, CustomMessageEntry):
            text = " ".join(c.content for c in entry.content if isinstance(c, TextContent))
            return f"custom:{entry.custom_type}", text
        if isinstance(entry, LabelEntry):
            return ("label", entry.label) if entry.label else None
        if isinstance(entry, ModelChangeEntry):
            return "model", f"{entry.provider_id}/{entry.model_id}"
        if isinstance(entry, ThinkingLevelChangeEntry):
            return "thinking_level", str(entry.thinking_level)
        if isinstance(entry, CustomInfoEntry):
            return f"info:{entry.custom_type}", ""
        return None

    disabled_ids: set[str] = set()

    def _contains_active(node: object) -> bool:
        """True if node or any descendant is on the active path."""
        stack = [node]
        while stack:
            n = stack.pop()
            if n.entry.id in active_ids:  # type: ignore[attr-defined]
                return True
            stack.extend(n.children)  # type: ignore[attr-defined]
        return False

    def _build_prefix(
        gutters: list[tuple[int, bool]],
        show_connector: bool,
        is_last: bool,
        display_indent: int,
    ) -> str:
        """Char-by-char prefix: gutters (│) + connector (├─/└─) + spaces."""
        if display_indent == 0:
            return ""
        connector_pos = display_indent - 1
        chars: list[str] = []
        for ci in range(display_indent * 3):
            level = ci // 3
            pos = ci % 3
            gutter_show = next((s for lv, s in gutters if lv == level), None)
            if gutter_show is not None:
                chars.append("│" if pos == 0 and gutter_show else " ")
            elif show_connector and level == connector_pos:
                if pos == 0:
                    chars.append("└" if is_last else "├")
                elif pos == 1:
                    chars.append("─")
                else:
                    chars.append(" ")
            else:
                chars.append(" ")
        return "".join(chars)

    def _emit_rows(root_nodes: list) -> None:
        """Build one row per displayable entry, in pre-order.

        Iterative, for the same reason `_contains_active` just above and
        `SessionManager.get_tree` are: a linear conversation is a chain one
        node deep per entry, so recursing per node raised

            RecursionError: maximum recursion depth exceeded

        on any session past roughly a thousand entries — `/tree` stopped
        working on exactly the long sessions it is most useful for. One real
        session reached a chain depth of 2390 against a limit of 1000.

        Children are pushed in reverse so popping reproduces the traversal
        order the recursion produced; the rows are byte-for-byte what they
        were.

        Layout rules, unchanged:
        - Connectors (├─/└─) only when multiple siblings exist.
        - Linear single-child chains stay flat (no indent increase, no connector).
        - Gutters (│) track open branch lines for descendants.
        """
        root_count = len(root_nodes)
        # (node, gutters, display_indent, is_branching, is_last). `is_branching`
        # and `is_last` describe the node's position among its own siblings,
        # which the recursion derived from the list it was called with.
        stack: list[tuple[object, list[tuple[int, bool]], int, bool, bool]] = [
            (node, [], 0, root_count > 1, i == root_count - 1) for i, node in enumerate(root_nodes)
        ]
        stack.reverse()

        while stack:
            node, gutters, display_indent, is_branching, is_last = stack.pop()
            entry = node.entry  # type: ignore[attr-defined]
            role_text = _entry_role_text(entry)

            if role_text is not None:
                role, text = role_text
                show_connector = is_branching and display_indent > 0
                prefix = _build_prefix(gutters, show_connector, is_last, display_indent)
                selectable = not isinstance(entry, MessageEntry) or _message_selectable(
                    entry.message
                )
                if not selectable:
                    disabled_ids.add(entry.id)
                rows.append(
                    TreeRow(
                        prefix=prefix,
                        role=role,
                        text=strip_control_chars(text[:snippet_budget]),
                        on_active_path=entry.id in active_ids,
                        is_current=entry.id == current_leaf,
                        selectable=selectable,
                        value=entry.id,
                        parent_value=getattr(entry, "parent_id", None),
                        has_children=len(node.children) > 0,  # type: ignore[attr-defined]
                    )
                )

            # Sort children so the branch containing the active leaf comes first
            children = node.children  # type: ignore[attr-defined]
            if len(children) > 1:
                children = sorted(children, key=lambda c: 0 if _contains_active(c) else 1)

            # Child indent rules:
            #   - node has multiple children → +1 (they will branch)
            #   - current level is branching AND not at root → +1 (just-branched grouping)
            #   - linear single-child chain → stay flat (no change)
            multiple_children = len(children) > 1
            if multiple_children or is_branching and display_indent > 0:
                child_indent = display_indent + 1
            else:
                child_indent = display_indent

            # Gutters: when this level branches, record a │ column for descendants
            if is_branching and display_indent > 0:
                child_gutters = gutters + [(display_indent - 1, not is_last)]
            else:
                child_gutters = gutters

            child_count = len(children)
            for i in range(child_count - 1, -1, -1):
                stack.append(
                    (
                        children[i],
                        child_gutters,
                        child_indent,
                        child_count > 1,
                        i == child_count - 1,
                    )
                )

    _emit_rows(nodes)

    if not rows:
        ctx.notify("No navigable branches found.")
        return

    def commit(entry_id: str) -> None:
        if entry_id in disabled_ids:
            ctx.notify(
                "Can't branch from a pending tool call —"
                " pick the tool result or a later message instead."
            )
            return
        asyncio.ensure_future(_apply_tree_branch(ctx, entry_id))

    ctx.layout.open_branch_tree_selector(
        rows, commit, lambda: ctx.notify("Branch navigation cancelled.")
    )


def _extract_user_message_text(message: object) -> str | None:
    """Return the text content of a UserMessage, or None if not a plain user message."""
    from tau.message.types import TextContent, UserMessage

    if not isinstance(message, UserMessage):
        return None
    contents = getattr(message, "contents", None)
    if not isinstance(contents, list):
        return None
    parts = [c.content for c in contents if isinstance(c, TextContent)]
    return " ".join(parts) if parts else None


async def _apply_tree_branch(ctx: CommandContext, entry_id: str) -> None:
    from tau.session.types import MessageEntry

    sm = ctx.runtime.session_manager
    settings = ctx.runtime.settings_manager

    # No-op if already at this node
    if sm is not None and sm.get_leaf_id() == entry_id:
        ctx.notify("Already at this point.")
        return

    # Detect if the selected entry is a user message — if so, navigate to its
    # parent and restore the message text into the editor instead of the history.
    navigate_id = entry_id
    restore_text: str | None = None
    if sm is not None and entry_id in sm.by_id:
        entry = sm.by_id[entry_id]
        if isinstance(entry, MessageEntry):
            user_text = _extract_user_message_text(entry.message)
            if user_text is not None:
                restore_text = user_text
                navigate_id = entry.parent_id or entry_id

    # Determine whether to ask about summarization
    summary_enabled = settings.is_branch_summary_enabled() if settings is not None else True
    skip_prompt = settings.get_branch_summary_skip_prompt() if settings is not None else False

    summarize = False
    if summary_enabled and not skip_prompt:
        from tau.tui.components.select_list import SelectItem

        summary_items: list[SelectItem[str]] = [
            SelectItem(
                label="No summary", description="Switch branch without summarizing", value="none"
            ),
            SelectItem(
                label="Summarize",
                description="Generate a summary of the abandoned branch",
                value="yes",
            ),
        ]
        fut, _commit, _cancel = selector_future()
        ctx.layout.open_tree_selector(summary_items, _commit, _cancel)
        choice = await fut

        if choice is None:
            return  # user cancelled

        summarize = choice == "yes"

    # Show spinner label while summarizing
    if summarize:
        ctx.layout.spinner.set_label("Summarizing branch…")

    try:
        # When restoring a user message, navigate to its parent
        # (navigate_id may differ from entry_id)
        if sm is not None and navigate_id != sm.get_leaf_id():
            ok = await ctx.runtime.navigate_tree(navigate_id, summarize=summarize)
            if not ok:
                ctx.notify("Branch navigation cancelled.")
                return
        elif restore_text is None:
            # Already at this node (assistant message case), covered above
            pass

        if restore_text is not None:
            # Branch summarization runs as an LLM call, so the await above can take a
            # while; don't clobber whatever the user typed into the editor in the
            # meantime — only restore into an empty editor.
            if not ctx.layout.get_editor_text().strip():
                ctx.layout.input.set_text(restore_text)
            ctx.notify("Restored message to input.")
        else:
            ctx.notify(f"Switched to branch at {entry_id[:8]}")
    except Exception as exc:
        ctx.notify(f"Failed to switch branch: {exc}")
    finally:
        if summarize:
            # Restore default spinner label
            ctx.layout.spinner.set_label(ctx.layout.spinner.theme.label_thinking)


def cmd_clone(ctx: CommandContext) -> None:
    asyncio.ensure_future(_apply_clone(ctx))


async def _apply_clone(ctx: CommandContext) -> None:
    try:
        await ctx.runtime.clone_session()
        sm = ctx.runtime.session_manager
        name = sm.session_file.name[:40] if sm and sm.session_file else "new session"
        ctx.notify(f"Cloned into {name}")
    except Exception as exc:
        ctx.notify(f"Failed to clone: {exc}")


def cmd_export(ctx: CommandContext, args: str = "") -> None:
    asyncio.ensure_future(_apply_export(ctx, args.strip()))


async def _apply_export(ctx: CommandContext, target: str) -> None:
    from pathlib import Path

    from tau.session.export import export_session_html

    sm = ctx.runtime.session_manager
    if sm is None:
        ctx.notify("No active session to export.")
        return

    if target:
        path = Path(target).expanduser()
        # A directory argument is a destination, not a filename to overwrite.
        if path.is_dir():
            path = path / f"{_export_stem(sm)}.html"
    else:
        path = Path.cwd() / f"{_export_stem(sm)}.html"

    try:
        # Rendering walks the whole branch and writes a file — keep both off
        # the event loop so a long transcript doesn't stall the UI.
        written = await asyncio.to_thread(export_session_html, sm, path)
    except Exception as exc:
        ctx.notify(f"Failed to export: {exc}")
        return
    ctx.notify(f"Exported to {written}")


def _export_stem(sm: Any) -> str:
    """A filename stem from the session name, falling back to its id."""
    import re

    name = (sm.get_session_name() or "").strip()
    if name:
        slug = re.sub(r"[^\w.-]+", "-", name).strip("-")
        if slug:
            return slug
    return sm.session_id or "session"


def cmd_session(ctx: CommandContext) -> None:
    from tau.session.stats import compute_session_stats
    from tau.tui.utils import BOLD, DIM, RESET

    sm = ctx.runtime.session_manager
    if sm is None:
        ctx.notify("No active session.")
        return

    stats = compute_session_stats(sm.get_branch())
    usage = stats.usage

    session_name = sm.get_session_name()
    session_file = sm.session_file
    session_id = sm.session_id or ""

    W = 14
    lines: list[str] = []
    lines.append(f"{BOLD}Session Info{RESET}")
    lines.append("")
    if session_name:
        lines.append(f"{DIM}{'Name':<{W}}{RESET} {session_name}")
    lines.append(f"{DIM}{'File':<{W}}{RESET} {session_file or 'in-memory'}")
    lines.append(f"{DIM}{'ID':<{W}}{RESET} {session_id}")
    lines.append("")
    lines.append(f"{BOLD}Messages{RESET}")
    lines.append(f"{DIM}{'User':<{W}}{RESET} {stats.user_messages}")
    lines.append(f"{DIM}{'Assistant':<{W}}{RESET} {stats.assistant_messages}")
    lines.append(f"{DIM}{'Tool calls':<{W}}{RESET} {stats.tool_calls}")
    lines.append(f"{DIM}{'Tool results':<{W}}{RESET} {stats.tool_results}")
    if stats.summaries:
        lines.append(f"{DIM}{'Summaries':<{W}}{RESET} {stats.summaries}")
    lines.append(f"{DIM}{'Total':<{W}}{RESET} {stats.total_messages}")
    lines.append("")
    lines.append(f"{BOLD}Tokens{RESET}")

    # Costs are the model's own per-million rates, applied when each response
    # landed — including the compaction and branch-summary calls, which are
    # billed like any other and used to be left out of this total entirely.
    lines.append(
        f"{DIM}{'Input':<{W}}{RESET} {format_number(usage.input_tokens)} (${usage.cost.input:.2f})"
    )
    lines.append(
        f"{DIM}{'Output':<{W}}{RESET} {format_number(usage.output_tokens)}"
        f" (${usage.cost.output:.2f})"
    )
    if usage.cache_read_tokens:
        lines.append(
            f"{DIM}{'Cache read':<{W}}{RESET} {format_number(usage.cache_read_tokens)}"
            f" (${usage.cost.cache_read:.2f})"
        )
    if usage.cache_write_tokens:
        lines.append(
            f"{DIM}{'Cache write':<{W}}{RESET} {format_number(usage.cache_write_tokens)}"
            f" (${usage.cost.cache_write:.2f})"
        )
    lines.append(
        f"{DIM}{'Total':<{W}}{RESET} {format_number(stats.total_tokens)} (${stats.total_cost:.2f})"
    )

    # Cache misses are already inside the total above; this says how much of it
    # was re-billed for a prefix that should have been read from cache.
    waste = stats.cache_waste
    if waste.miss_count:
        causes = []
        if waste.expired_count:
            causes.append(f"{waste.expired_count} after an idle gap")
        if waste.model_change_count:
            causes.append(f"{waste.model_change_count} after a model switch")
        detail = f" - {', '.join(causes)}" if causes else ""
        misses = "1 miss" if waste.miss_count == 1 else f"{waste.miss_count} misses"
        lines.append("")
        lines.append(f"{BOLD}Cache{RESET}")
        lines.append(
            f"{DIM}{'Re-billed':<{W}}{RESET} {format_number(waste.missed_tokens)}"
            f" (${waste.missed_cost:.2f}) across {misses}{detail}"
        )

    ctx.notify("\n".join(lines))


async def open_search_selector(ctx: CommandContext, query: str) -> None:
    """Resume a session found by what was said in it, not its name or date.

    This is the resume picker with its list pre-filtered to sessions that
    contain ``query``, rather than a separate surface: picking a result resumes
    it exactly as `/resume` would.
    """

    from tau.session.manager import SessionManager
    from tau.session.search import search_sessions

    query = query.strip()
    if not query:
        ctx.notify('Usage: /search <text>  — e.g. /search "compaction race"')
        return

    sm = ctx.runtime.session_manager
    cwd = sm.cwd if sm is not None else None
    current_path = sm.session_file if sm is not None else None
    current_id = getattr(sm, "session_id", None) if sm is not None else None

    def _run() -> tuple[list, dict[str, str]]:
        sessions = SessionManager.list(cwd) if cwd else SessionManager.list_all()
        hits = search_sessions(query, sessions=sessions)
        # One entry per session, newest first, keeping the first snippet as the
        # reason it matched — the picker lists sessions, not entries.
        matched: list = []
        snippets: dict[str, str] = {}
        for hit in hits:
            key = str(hit.session.path)
            if key in snippets:
                continue
            snippets[key] = hit.snippet
            matched.append(hit.session)
        return matched, snippets

    try:
        matched, snippets = await asyncio.to_thread(_run)
    except Exception:
        ctx.notify("Search failed; see the session log.")
        return

    if not matched:
        ctx.notify(f"No session contains {query!r}.")
        return

    def commit(session: object) -> None:
        asyncio.ensure_future(_apply_resume(ctx, session))

    ctx.layout.open_resume_selector(
        sessions=[],
        loading=True,
        on_commit=commit,
        on_cancel=lambda: ctx.notify("Search cancelled."),
        all_sessions_loader=lambda: [],
        on_load_all=lambda: None,
        on_load_more=lambda _scope: None,
        current_session_path=current_path,
        current_session_id=current_id,
    )
    ctx.layout.append_resume_sessions("current", matched, False, len(matched))
    plural = "session" if len(matched) == 1 else "sessions"
    ctx.notify(f"{len(matched)} {plural} matching {query!r}.")
