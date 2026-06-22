from __future__ import annotations

import asyncio

from tau.tui.commands.context import CommandContext


def open_resume_selector(ctx: CommandContext) -> None:
    from tau.session.manager import SessionManager

    sm = ctx.runtime.session_manager
    cwd = sm.cwd if sm is not None else None
    current_path = sm.session_file if sm is not None else None

    current_sessions = SessionManager.list(cwd) if cwd is not None else []

    def all_loader() -> list:
        return SessionManager.list_all()

    def commit(path: object) -> None:
        from pathlib import Path

        asyncio.ensure_future(_apply_resume(ctx, Path(str(path))))

    ctx.layout.open_resume_selector(
        sessions=current_sessions,
        on_commit=commit,
        on_cancel=lambda: ctx.notify("Resume cancelled."),
        all_sessions_loader=all_loader,
        current_session_path=current_path,
    )


async def _apply_resume(ctx: CommandContext, path: object) -> None:
    from pathlib import Path

    p = Path(str(path))
    try:
        await ctx.runtime.resume_session(p)
        ctx.notify(f"Resumed session {p.stem[:32]}")
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
    from tau.tui.components.tree_select_list import TreeRow

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

    # Flatten once to map id -> parent_id, so we can walk current_leaf's
    # ancestor chain and mark the active path (independent of tree nesting).
    parent_of: dict[str, str | None] = {}

    def _index(node_list: list) -> None:
        for node in node_list:
            parent_of[node.entry.id] = node.entry.parent_id
            _index(node.children)

    _index(nodes)

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

    def _walk(
        node_list: list,
        gutters: list[tuple[int, bool]],
        display_indent: int,
        just_branched: bool,
    ) -> None:
        """
        Tree walk:
        - Connectors (├─/└─) only when multiple siblings exist.
        - Linear single-child chains stay flat (no indent increase, no connector).
        - Gutters (│) track open branch lines for descendants.
        - just_branched: parent had multiple children → first gen after branch also indents.
        """
        n = len(node_list)
        is_branching = n > 1  # multiple siblings → show connectors

        for i, node in enumerate(node_list):
            is_last = i == n - 1
            entry = node.entry
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
                        text=text[:80].replace("\n", " ").replace("\t", " "),
                        on_active_path=entry.id in active_ids,
                        is_current=entry.id == current_leaf,
                        selectable=selectable,
                        value=entry.id,
                        parent_value=getattr(entry, "parent_id", None),
                        has_children=len(node.children) > 0,
                    )
                )

            # Sort children so the branch containing the active leaf comes first
            children = node.children
            if len(children) > 1:
                children = sorted(children, key=lambda n: 0 if _contains_active(n) else 1)

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

            _walk(children, child_gutters, child_indent, just_branched=is_branching)

    _walk(nodes, [], 0, just_branched=False)

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
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str | None] = loop.create_future()

        def _commit(value: str) -> None:
            if not fut.done():
                fut.set_result(value)

        def _cancel() -> None:
            if not fut.done():
                fut.set_result(None)

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
            ctx.layout.input.set_text(restore_text)
            ctx.notify("Restored message to input.")
        else:
            ctx.notify(f"Switched to branch at {entry_id[:8]}")
    except Exception as exc:
        ctx.notify(f"Failed to switch branch: {exc}")
    finally:
        if summarize:
            # Restore default spinner label
            ctx.layout.spinner.set_label(ctx.layout.spinner._theme.label_thinking)


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


def cmd_session(ctx: CommandContext) -> None:
    from tau.message.types import AssistantMessage, ToolMessage, UserMessage
    from tau.session.types import MessageEntry as SessionMessageEntry
    from tau.tui.ansi import BOLD, DIM, RESET

    sm = ctx.runtime.session_manager
    if sm is None:
        ctx.notify("No active session.")
        return

    branch = sm.get_branch()

    user_count = 0
    assistant_count = 0
    tool_call_count = 0
    tool_result_count = 0
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    total_cost = 0.0

    for entry in branch:
        if not isinstance(entry, SessionMessageEntry):
            continue
        msg = entry.message
        if isinstance(msg, UserMessage):
            user_count += 1
        elif isinstance(msg, AssistantMessage):
            assistant_count += 1
            tool_call_count += len(msg.tool_calls())
            input_tokens += msg.usage.input_tokens
            output_tokens += msg.usage.output_tokens
            cache_read_tokens += msg.usage.cache_read_tokens
            cache_write_tokens += msg.usage.cache_write_tokens
            total_cost += msg.usage.cost.total
        elif isinstance(msg, ToolMessage):
            tool_result_count += len(msg.contents)

    total_messages = user_count + assistant_count + (1 if tool_result_count else 0)
    total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens

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
    lines.append(f"{DIM}{'User':<{W}}{RESET} {user_count}")
    lines.append(f"{DIM}{'Assistant':<{W}}{RESET} {assistant_count}")
    lines.append(f"{DIM}{'Tool calls':<{W}}{RESET} {tool_call_count}")
    lines.append(f"{DIM}{'Tool results':<{W}}{RESET} {tool_result_count}")
    lines.append(f"{DIM}{'Total':<{W}}{RESET} {total_messages}")
    lines.append("")
    lines.append(f"{BOLD}Tokens{RESET}")
    # Helper to format token counts with K/M/B/T suffixes
    def _human_readable(num: int) -> str:
        """Human‑readable token count using K/M/B/T.

        For values < 1 000 we keep the integer representation; otherwise we
        divide by 1 000 repeatedly and attach the appropriate suffix. One decimal
        place is retained for readability (e.g. 1.2K, 3.4M).
        """
        if num < 1_000:
            return f"{num}"
        suffixes = ["", "K", "M", "B", "T"]
        i = 0
        value = float(num)
        while value >= 1_000 and i < len(suffixes) - 1:
            value /= 1_000
            i += 1
        # Drop decimal if the value is an integer after division
        if value.is_integer():
            return f"{int(value)}{suffixes[i]}"
        return f"{value:.1f}{suffixes[i]}"

    # Cost rates per token (USD). Use defaults if the LLM didn't report a cost.
    INPUT_RATE = 0.0001 / 1_000  # $0.0001 per 1 k input tokens
    OUTPUT_RATE = 0.0002 / 1_000  # $0.0002 per 1 k output tokens

    # Token counts with human‑readable format and inline cost (two decimals)
    lines.append(f"{DIM}{'Input':<{W}}{RESET} {_human_readable(input_tokens)} (${input_tokens * INPUT_RATE:.2f})")
    lines.append(f"{DIM}{'Output':<{W}}{RESET} {_human_readable(output_tokens)} (${output_tokens * OUTPUT_RATE:.2f})")
    if cache_read_tokens:
        lines.append(f"{DIM}{'Cache read':<{W}}{RESET} {_human_readable(cache_read_tokens)}")
    if cache_write_tokens:
        lines.append(f"{DIM}{'Cache write':<{W}}{RESET} {_human_readable(cache_write_tokens)}")
    # Total tokens and total cost (use provided total_cost if available, else estimate)
    total_cost_est = total_cost if total_cost > 0 else (input_tokens * INPUT_RATE + output_tokens * OUTPUT_RATE)
    lines.append(f"{DIM}{'Total':<{W}}{RESET} {_human_readable(total_tokens)} (${total_cost_est:.2f})")


    ctx.notify("\n".join(lines))
