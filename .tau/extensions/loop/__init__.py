"""loop extension — a single `/loop` command for recurring prompts in Tau.

Modeled on pi-scheduler (https://github.com/manojlds/pi-scheduler)'s runtime
design (interval parsing, id-derived jitter, 3-day auto-expiry, idle-gated
dispatch, atomic disk persistence) but trimmed to exactly what's needed here:
one command, no one-time reminders, no LLM-callable tool.

Usage:
    /loop                         open the interactive loop manager (TUI only)
    /loop <period> <task>         create directly, e.g. /loop 5m water the plants
    /loop <task> every <period>   create directly, e.g. /loop water the plants every 2h

The manager lists every loop and lets you toggle enabled/disabled, edit the
instruction or duration, or delete it — all through the standard select /
prompt / editor overlays. In headless mode (no TUI), bare /loop just prints
the current list as text.

Tasks persist to .tau/loop/scheduler.json and survive session restarts.
A loop only fires while Tau is idle between turns (never mid-turn), and
auto-expires 3 days after creation.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .dispatch import _emit, _update_status, run_ticker
from .duration import (
    format_duration,
    parse_duration,
    parse_loop_args,
)
from .state import MAX_TASKS, LoopTask, SchedulerState

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext
    from tau.modes.interactive.ui_context import UIContext

_USAGE = "Usage: /loop | /loop <period> <task> | /loop <task> every <period>"

_COMMON_DURATIONS = ["5m", "10m", "15m", "30m", "1h", "2h", "6h", "12h", "1d"]


def _get_argument_completions(prefix: str):
    from tau.tui.autocomplete import AutocompleteItem

    if " " in prefix.strip():
        return []
    return [
        AutocompleteItem(label=d, description="run every…")
        for d in _COMMON_DURATIONS
        if d.startswith(prefix.strip())
    ]


def _loop_label(task: LoopTask) -> str:
    glyph = "■" if task.enabled else "☐"
    duration = format_duration(task.interval_s)
    preview = task.prompt if len(task.prompt) <= 56 else f"{task.prompt[:53]}..."
    return f"{glyph} {task.id}  every {duration}  —  {preview}"


def _task_id_from_label(label: str) -> str:
    # "■ abc12345  every 5m  —  preview" -> "abc12345"
    return label.split(maxsplit=2)[1]


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
            _emit(ctx, f"Loop limit reached ({MAX_TASKS}). Delete one via /loop first.", "error")
            return

        task = state.add(parsed["prompt"], parsed["interval_s"])
        _emit(
            ctx,
            f"Loop scheduled every {format_duration(parsed['interval_s'])} "
            f"(id: {task.id}). Expires in 3 days.",
        )
        if parsed.get("note"):
            _emit(ctx, parsed["note"])
        _update_status(ctx, state)

    async def _manage_loop(ctx: ExtensionContext, ui: UIContext, task: LoopTask) -> None:
        """Action menu for a single loop. Returns to the list when done."""
        while True:
            task = state.tasks.get(task.id)  # re-fetch in case it changed/was deleted
            if task is None:
                return
            actions = [
                "Disable" if task.enabled else "Enable",
                "Edit instruction",
                "Edit duration",
                "Delete",
                "Back",
            ]
            choice = await ui.select(
                f"Loop {task.id} — every {format_duration(task.interval_s)}", actions
            )
            if choice is None or choice == "Back":
                return

            if choice in ("Enable", "Disable"):
                state.set_enabled(task.id, choice == "Enable")
                ui.notify(f"{choice}d loop {task.id}.")
                _update_status(ctx, state)
                continue

            if choice == "Edit instruction":
                text = await ui.editor("Edit loop instruction", prefill=task.prompt)
                if text is not None and text.strip():
                    state.update_prompt(task.id, text.strip())
                    ui.notify(f"Updated instruction for loop {task.id}.")
                continue

            if choice == "Edit duration":
                text = await ui.prompt(f"New duration for {task.id} (e.g. 5m, 2h)")
                if text is None or not text.strip():
                    continue
                secs = parse_duration(text.strip())
                if not secs:
                    ui.notify(f"Couldn't parse duration: {text.strip()}", "warning")
                    continue
                state.update_interval(task.id, secs)
                ui.notify(f"Loop {task.id} now runs every {format_duration(secs)}.")
                _update_status(ctx, state)
                continue

            if choice == "Delete":
                ok = await ui.confirm(f"Delete loop {task.id}?", task.prompt)
                if ok:
                    state.delete(task.id)
                    ui.notify(f"Deleted loop {task.id}.")
                    _update_status(ctx, state)
                    return
                continue

    async def _show_picker(ctx: ExtensionContext, ui: UIContext) -> None:
        while True:
            options = [
                _loop_label(t) for t in sorted(state.tasks.values(), key=lambda t: t.next_run_at)
            ]
            options.append("+ New loop")
            if state.tasks:
                options.append("Clear all loops")

            choice = await ui.select("Loops", options)
            if choice is None:
                return

            if choice == "+ New loop":
                text = await ui.prompt("New loop (e.g. '5m water the plants')")
                if text is not None and text.strip():
                    _create_loop(ctx, text.strip())
                continue

            if choice == "Clear all loops":
                ok = await ui.confirm(
                    "Clear all loops?", f"This deletes all {len(state.tasks)} loop(s)."
                )
                if ok:
                    n = state.clear()
                    ui.notify(f"Cleared {n} loop{'s' if n != 1 else ''}.")
                    _update_status(ctx, state)
                continue

            task_id = _task_id_from_label(choice)
            task = state.tasks.get(task_id)
            if task is not None:
                await _manage_loop(ctx, ui, task)

    async def cmd_loop(ctx: ExtensionContext, args: list[str]):
        if not args:
            ui = ctx.ui
            if ui is None:
                _emit(ctx, state.format_list())
                return
            await _show_picker(ctx, ui)
            return

        _create_loop(ctx, " ".join(args))

    tau.register_command(
        "loop",
        "Manage recurring prompts: /loop opens the loop manager, "
        "/loop <period> <task> creates one directly",
        cmd_loop,
        argument_hint="<period> <task>",
        get_argument_completions=_get_argument_completions,
    )
