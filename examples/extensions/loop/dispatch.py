"""Idle-gated dispatch loop: ticks every second, fires due loops when idle."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from state import LoopTask, SchedulerState

from tau.extensions.context import StaleExtensionContextError

if TYPE_CHECKING:
    from tau.extensions.context import ExtensionContext


def _emit(ctx: ExtensionContext, text: str, level: str = "info") -> None:
    if ctx.ui is not None:
        ctx.ui.notify(text, level)
    else:
        print(text)


def _update_status(ctx: ExtensionContext, state: SchedulerState) -> None:
    if not ctx.has_ui or ctx.ui is None:
        return
    if not state.tasks:
        ctx.ui.clear_status("loop-scheduler")
        return
    enabled = [t for t in state.tasks.values() if t.enabled]
    if not enabled:
        n = len(state.tasks)
        ctx.ui.set_status("loop-scheduler", f"⏸ {n} loop{'s' if n != 1 else ''} paused")
        return
    next_run_at = min(t.next_run_at for t in enabled)
    import datetime

    next_str = datetime.datetime.fromtimestamp(next_run_at).strftime("%H:%M")
    ctx.ui.set_status("loop-scheduler", f"⏰ {len(enabled)} active • next {next_str}")


async def _dispatch(ctx: ExtensionContext, state: SchedulerState, task: LoopTask) -> None:
    if not task.enabled:
        return
    now = time.time()
    try:
        await ctx.send_user_message(task.prompt, deliver_as="steer", trigger_turn=True)
    except Exception:
        task.pending = True
        task.last_status = "error"
        state._persist()
        return

    task.pending = False
    task.last_run_at = now
    task.last_status = "success"
    task.run_count += 1

    next_run_at = task.next_run_at
    while next_run_at <= now:
        next_run_at += task.interval_s
    task.next_run_at = next_run_at
    state._persist()
    _update_status(ctx, state)


async def _tick(ctx: ExtensionContext, state: SchedulerState) -> None:
    now = time.time()
    mutated = False

    for task in list(state.tasks.values()):
        if task.expires_at and now >= task.expires_at:
            del state.tasks[task.id]
            mutated = True
            continue
        if task.enabled and now >= task.next_run_at:
            task.pending = True

    if mutated:
        state._persist()
    _update_status(ctx, state)

    if state.dispatching:
        return
    if not ctx.is_idle() or ctx.has_pending_messages():
        return

    pending = [t for t in state.tasks.values() if t.enabled and t.pending]
    if not pending:
        return

    next_task = min(pending, key=lambda t: t.next_run_at)
    state.dispatching = True
    try:
        await _dispatch(ctx, state, next_task)
    finally:
        state.dispatching = False


async def run_ticker(ctx: ExtensionContext, state: SchedulerState) -> None:
    try:
        while True:
            await asyncio.sleep(1)
            await _tick(ctx, state)
    except StaleExtensionContextError:
        return  # session replaced/reloaded — a fresh ticker starts from session_start
