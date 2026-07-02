from __future__ import annotations

import re
from typing import Any

from .recall import run_recall
from .state import STATE


def register_commands(tau: Any) -> None:
    tau.register_command(
        "vcc",
        "Compact the conversation now with the algorithmic vcc summarizer (no LLM).",
        _cmd_vcc,
        argument_hint="[focus note]",
    )
    tau.register_command(
        "vcc-recall",
        "Search compacted history and feed the results to the agent as context.",
        _cmd_recall,
        aliases=["recall"],
        argument_hint="<query> [scope:all] [page:N]",
    )


async def _cmd_vcc(ctx: Any, args: list[str]) -> None:
    """Trigger a one-shot algorithmic compaction regardless of the setting."""
    runtime = getattr(ctx, "_runtime", None)
    agent = getattr(runtime, "agent", None) if runtime is not None else None
    if agent is None:
        return

    custom = " ".join(args).strip() or None
    STATE.force_next = True
    try:
        did_compact = await agent.compact(custom_instructions=custom)
    finally:
        # Never let a leftover force flag hijack the next compaction.
        STATE.force_next = False

    if ctx.ui is not None:
        ctx.ui.notify(
            "vcc: compaction complete."
            if did_compact
            else "vcc: nothing to compact — conversation is too short."
        )


_SCOPE_RE = re.compile(r"^scope:(active|all)$", re.I)
_PAGE_RE = re.compile(r"^page:(\d+)$", re.I)


def _parse_recall_args(args: list[str]) -> tuple[str, str, int]:
    """Return ``(query, scope, page)`` from raw tokens."""
    scope = "active"
    page = 1
    terms: list[str] = []
    for tok in args:
        if m := _SCOPE_RE.match(tok):
            scope = m.group(1).lower()
        elif m := _PAGE_RE.match(tok):
            page = max(1, int(m.group(1)))
        else:
            terms.append(tok)
    return " ".join(terms).strip(), scope, page


async def _cmd_recall(ctx: Any, args: list[str]) -> None:
    query, scope, page = _parse_recall_args(args)
    runtime = getattr(ctx, "_runtime", None)
    sm = getattr(runtime, "session_manager", None) if runtime is not None else None
    if sm is None:
        return

    entries = sm.get_entries() if scope == "all" else sm.get_branch()
    result = run_recall(entries, query=query or None, page=page)

    header = f"vcc_recall ({scope}) — {query!r}" if query else f"vcc_recall ({scope}) — recent"
    await ctx.send_user_message(
        f"<vcc_recall>\n{header}\n\n{result}\n</vcc_recall>",
        deliver_as="follow_up",
    )
    if ctx.ui is not None:
        ctx.ui.notify(f"vcc_recall: results fed to the agent ({scope}).")
