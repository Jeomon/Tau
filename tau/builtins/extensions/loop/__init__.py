"""loop extension — a single `/loop` command for recurring prompts in Tau.

Modeled on pi-scheduler (https://github.com/manojlds/pi-scheduler)'s runtime
design (interval parsing, id-derived jitter, 3-day auto-expiry, idle-gated
dispatch, atomic disk persistence) but trimmed to exactly what's needed here:
one command, no one-time reminders, no TUI manager, no LLM-callable tool.

Usage:
    /loop <task>                 recurring, default interval (10m)
    /loop 5m <task>               recurring every 5m
    /loop <task> every 2h         recurring every 2h
    /loop list                    list all loops
    /loop enable <id>             re-enable a disabled loop
    /loop disable <id>            pause a loop without deleting it
    /loop remove <id>             delete a loop
    /loop clear                   delete all loops

Tasks persist to .tau/loop-scheduler.json and survive session restarts.
A loop only fires while Tau is idle between turns (never mid-turn), and
auto-expires 3 days after creation.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))

from dispatch import _emit, _update_status, run_ticker  # type: ignore[import-not-found]
from duration import format_duration, parse_loop_args  # type: ignore[import-not-found]
from state import MAX_TASKS, SchedulerState  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext

_RESERVED = {"create", "list", "clear", "enable", "disable", "remove", "delete", "rm"}

_USAGE = (
    "Usage: /loop <task> | /loop 5m <task> | /loop <task> every 2h | /loop create <task> <duration> | "
    "/loop list | /loop enable <id> | /loop disable <id> | /loop remove <id> | /loop clear"
)


def _get_argument_completions(prefix: str):
    from tau.tui.autocomplete import AutocompleteItem

    parts = prefix.split()
    if parts and (len(parts) > 1 or prefix.endswith(" ")):
        return []
    p = parts[0] if parts else ""
    return [
        AutocompleteItem(label=word, description="loop subcommand")
        for word in sorted(_RESERVED - {"delete", "rm"})
        if word.startswith(p)
    ]


def register(tau: ExtensionAPI) -> None:
    state = SchedulerState()
    ticker: dict[str, asyncio.Task] = {}

    @tau.on("session_start")
    async def on_start(event, ctx: ExtensionContext):
        state.set_storage(ctx.cwd)
        old = ticker.get("task")
        if old and not old.done():
            old.cancel()
        ticker["task"] = asyncio.ensure_future(run_ticker(ctx, state))
        _update_status(ctx, state)

    @tau.on("session_shutdown")
    async def on_shutdown(event, ctx: ExtensionContext):
        task = ticker.pop("task", None)
        if task:
            task.cancel()
        if ctx.ui is not None:
            ctx.ui.clear_status("loop-scheduler")

    def _create_loop(ctx: ExtensionContext, raw: str) -> None:
        parsed = parse_loop_args(raw)
        if not parsed:
            _emit(ctx, _USAGE, "warning")
            return

        if len(state.tasks) >= MAX_TASKS:
            _emit(ctx, f"Loop limit reached ({MAX_TASKS}). Remove one with /loop remove <id>.", "error")
            return

        task = state.add(parsed["prompt"], parsed["interval_s"])
        _emit(ctx, f"Loop scheduled every {format_duration(parsed['interval_s'])} (id: {task.id}). Expires in 3 days.")
        if parsed.get("note"):
            _emit(ctx, parsed["note"])
        _update_status(ctx, state)

    async def cmd_loop(ctx: ExtensionContext, args: list[str]):
        if not args:
            _emit(ctx, _USAGE, "warning")
            return

        first = args[0].lower()

        if first in _RESERVED:
            if first == "create":
                raw = " ".join(args[1:]).strip()
                if not raw:
                    _emit(ctx, "Usage: /loop create <task> <duration>", "warning")
                    return
                _create_loop(ctx, raw)
                return
            if first == "list":
                _emit(ctx, state.format_list())
                return
            if first == "clear":
                n = state.clear()
                _emit(ctx, f"Cleared {n} loop{'s' if n != 1 else ''}.")
                _update_status(ctx, state)
                return
            if first in ("enable", "disable"):
                if len(args) < 2:
                    _emit(ctx, f"Usage: /loop {first} <id>", "warning")
                    return
                task_id = args[1]
                enabled = first == "enable"
                ok = state.set_enabled(task_id, enabled)
                if not ok:
                    _emit(ctx, f"Loop not found: {task_id}", "warning")
                    return
                _emit(ctx, f"{'Enabled' if enabled else 'Disabled'} loop {task_id}.")
                _update_status(ctx, state)
                return
            if first in ("remove", "delete", "rm"):
                if len(args) < 2:
                    _emit(ctx, "Usage: /loop remove <id>", "warning")
                    return
                task_id = args[1]
                ok = state.delete(task_id)
                if not ok:
                    _emit(ctx, f"Loop not found: {task_id}", "warning")
                    return
                _emit(ctx, f"Removed loop {task_id}.")
                _update_status(ctx, state)
                return

        # Anything else: create a new loop.
        _create_loop(ctx, " ".join(args))

    tau.register_command(
        "loop",
        "Schedule/manage recurring prompts: /loop <task> | 5m <task> | create <task> <duration> | "
        "list | enable/disable/remove <id> | clear",
        cmd_loop,
        argument_hint="<task> | 5m <task> | create <task> <duration> | list | enable <id> | disable <id> | remove <id> | clear",
        get_argument_completions=_get_argument_completions,
    )
