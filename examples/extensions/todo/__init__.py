"""todo extension — a todo list tool with an above-editor status board.

Ported from the pi coding agent's reference todo extension and pi-todo-lite:
a single `todo` tool with create/update/list/get/delete/clear actions, state
rebuilt by replaying the session's custom entries (so it naturally follows
branch/fork navigation), plus a `/todos` command for a quick look.

The task list itself is surfaced through a board widget above the input box
rather than the tool call/result in the transcript — the board only appears
while there is at least one pending task, and disappears once the list is
empty or fully done.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).parent))

from todo_tool import TodoState, TodoTool

from tau.tui.component import Component

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect

WIDGET_KEY = "todo"


class TodoBoardWidget(Component):
    """Mutable above-editor line block; content is pushed in by TodoBoard."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def set_lines(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        row = 0
        for line in self._lines:
            row += parse_ansi_wrapped_into(buf, area.x, area.y + row, line, area.width)
        return row


class TodoBoard:
    """Shows/hides/updates the above-editor board based on pending tasks."""

    def __init__(self, state: TodoState) -> None:
        self._state = state
        self._widget: TodoBoardWidget | None = None
        self._shown = False

    def _lines(self) -> list[str]:
        from tau.tui.utils import BOLD, DIM, GREEN, RESET, YELLOW

        pending = self._state.list("pending")
        header = f"{YELLOW}☐{RESET} {BOLD}Todos ({len(pending)} pending){RESET}"
        lines = [header]
        for item in self._state.items:
            glyph = f"{GREEN}✓{RESET}" if item.done else f"{DIM}☐{RESET}"
            lines.append(f"  {glyph} {item.id}. {item.subject}")
        return lines

    def sync(self, ctx: ExtensionContext) -> None:
        ui = ctx.ui
        if ui is None:
            return
        pending = self._state.list("pending")
        if not pending:
            if self._shown:
                ui.remove_widget(WIDGET_KEY)
                self._shown = False
            return
        if self._widget is None:
            self._widget = TodoBoardWidget()
        self._widget.set_lines(self._lines())
        if not self._shown:
            ui.set_widget(WIDGET_KEY, self._widget, placement="above_editor")
            self._shown = True
        else:
            ui.request_render()

    def hide(self, ctx: ExtensionContext) -> None:
        ui = ctx.ui
        if ui is not None and self._shown:
            ui.remove_widget(WIDGET_KEY)
        self._shown = False
        self._widget = None


AUTO_CONTINUE_WIDGET_KEY = "todo-autocontinue"


class TodoAutoContinue:
    """Forces the agent to keep working through pending tasks between turns.

    Mirrors pi-til-done's design: on agent_end, if pending tasks remain and
    nothing is already queued, wait out a short interruptible countdown, then
    inject a follow-up turn naming the next task. A circuit breaker caps
    consecutive no-progress cycles — but any todo mutation resets it (matching
    pi-til-done's own reset-on-mutation semantics), so genuine progress on a
    long plan is never capped.
    """

    MAX_ITERATIONS = 20
    COUNTDOWN_SECONDS = 3

    def __init__(self, state: TodoState, runtime_ref: Any, enabled: bool) -> None:
        self._state = state
        self._runtime_ref = runtime_ref
        self._enabled = enabled
        self._count = 0
        self._task: Any = None

    def _cancel_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def reset(self, ctx: ExtensionContext | None = None) -> None:
        """Call after any real todo mutation — clears the no-progress counter."""
        self._count = 0
        self._cancel_task()
        if ctx is not None:
            ui = ctx.ui
            if ui is not None:
                ui.remove_widget(AUTO_CONTINUE_WIDGET_KEY)

    def stop(self, ctx: ExtensionContext | None = None) -> None:
        """Call on session shutdown/switch — same as reset, kept as a separate name for clarity."""
        self.reset(ctx)

    def on_agent_end(self, event: Any, ctx: ExtensionContext) -> None:
        if not self._enabled:
            return
        from tau.hooks.engine import AgentEndReason

        if getattr(event, "reason", AgentEndReason.Completed) != AgentEndReason.Completed:
            return
        pending = self._state.list("pending")
        if not pending:
            self._count = 0
            return
        if ctx.has_pending_messages():
            return

        self._cancel_task()
        self._count += 1
        if self._count > self.MAX_ITERATIONS:
            self._count = 0
            ui = ctx.ui
            if ui is not None:
                ui.notify(
                    f"Todo auto-continue paused after {self.MAX_ITERATIONS} cycles with no "
                    f"progress — {len(pending)} task(s) still pending. Take over manually.",
                    type="warning",
                )
            return

        import asyncio

        self._task = asyncio.ensure_future(self._countdown(pending[0], len(pending)))

    async def _countdown(self, next_item: Any, pending_count: int) -> None:
        import asyncio

        from tau.extensions.context import ExtensionContext

        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return

        try:
            for remaining in range(self.COUNTDOWN_SECONDS, 0, -1):
                ctx = ExtensionContext.from_runtime(runtime)
                ui = ctx.ui
                if ui is not None:
                    ui.set_widget(
                        AUTO_CONTINUE_WIDGET_KEY,
                        [f"⏳ Continuing in {remaining}s… (send a message to cancel)"],
                        placement="above_editor",
                    )
                await asyncio.sleep(1)

            ctx = ExtensionContext.from_runtime(runtime)
            ui = ctx.ui
            if ui is not None:
                ui.remove_widget(AUTO_CONTINUE_WIDGET_KEY)
            if not ctx.is_idle() or ctx.has_pending_messages():
                return

            prompt = (
                f"{pending_count} todo task(s) are still pending. Continue with the next one: "
                f"#{next_item.id} {next_item.subject}. Mark it done via the todo tool once "
                f"finished, then move on to the next pending task."
            )
            await ctx.send_user_message(prompt, deliver_as="follow_up", trigger_turn=True)
        except asyncio.CancelledError:
            runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
            if runtime is not None:
                ctx = ExtensionContext.from_runtime(runtime)
                ui = ctx.ui
                if ui is not None:
                    ui.remove_widget(AUTO_CONTINUE_WIDGET_KEY)
            raise


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    state = TodoState()
    board = TodoBoard(state)
    auto_continue = TodoAutoContinue(
        state, tau._runtime_ref, enabled=bool(config.get("auto_continue", True))
    )

    def _rebuild(_event: Any, ctx: ExtensionContext) -> None:
        state.rebuild(ctx.branch_entries)
        board.sync(ctx)
        auto_continue.reset(ctx)

    tau.on("session_start", _rebuild)
    tau.on("session_tree", _rebuild)

    def _on_tui_ready(_event: Any, ctx: ExtensionContext) -> None:
        board.sync(ctx)

    tau.on("tui_ready", _on_tui_ready)

    def _on_shutdown(_event: Any, ctx: ExtensionContext) -> None:
        board.hide(ctx)
        auto_continue.stop(ctx)

    tau.on("session_shutdown", _on_shutdown)

    tau.on("agent_end", auto_continue.on_agent_end)

    def _on_mutate(ctx: ExtensionContext) -> None:
        board.sync(ctx)
        auto_continue.reset(ctx)

    tau.register_tool(TodoTool(state, tau._runtime_ref, on_mutate=_on_mutate))

    async def cmd_todos(ctx: ExtensionContext, _args: list[str]) -> None:
        ui = ctx.ui
        if ui is None:
            return
        if not state.items:
            ui.notify("No todos yet. Ask the agent to add some!")
            return
        pending = [i for i in state.items if not i.done]
        done = [i for i in state.items if i.done]
        header = " · ".join(
            part
            for part in (f"{len(done)} done" if done else "", f"{len(pending)} pending" if pending else "")
            if part
        )
        lines = [header]
        if pending:
            lines.append("── Pending ──")
            lines += [f"  ○ {i.line()}" for i in pending]
        if done:
            lines.append("── Done ──")
            lines += [f"  ✓ {i.line()}" for i in done]
        ui.notify("\n".join(lines))

    tau.register_command(
        "todos",
        "Show the current todo list",
        cmd_todos,
        requires_idle=False,
    )
