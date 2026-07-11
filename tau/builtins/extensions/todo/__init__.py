"""todo extension — a todo list tool with an above-editor status board.

Ported from the pi coding agent's reference todo extension and pi-todo-lite:
a single `todo` tool with create/update/list/get/delete/clear actions, state
rebuilt by replaying the session's custom entries (so it naturally follows
branch/fork navigation), plus a `/todos` command for a quick look.

The task list itself is surfaced through a board widget above the input box
rather than the tool call/result in the transcript — the board appears as
soon as a task exists and stays up (showing done/failed tasks too) until the
list is explicitly cleared or every task is deleted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).parent))

from todo_tool import TodoState, TodoTool  # type: ignore[import-not-found]

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

    def _lines(self, theme: Any) -> list[str]:
        from tau.modes.interactive.components.message_list import apply_render_shell
        from tau.tui.style import Style, apply_style

        glyphs = {
            "pending": ("☐", theme.muted),
            "in_progress": ("■", theme.warning),
            "done": ("✓", theme.success),
            "failed": ("✗", theme.error),
        }
        items = []
        for item in self._state.items:
            glyph, style = glyphs[item.status]
            subject = item.subject
            if item.status == "done":
                subject = apply_style(theme.muted.patch(Style().dim().strikethrough()), subject)
            items.append(f"{apply_style(style, glyph)} {item.id}. {subject}")
        return apply_render_shell(items, theme.message)

    def sync(self, ctx: ExtensionContext) -> None:
        ui = ctx.ui
        if ui is None:
            return
        if not self._state.items:
            if self._shown:
                ui.remove_widget(WIDGET_KEY)
                self._shown = False
            return
        theme = ui.theme
        if theme is None:
            return
        if self._widget is None:
            self._widget = TodoBoardWidget()
        self._widget.set_lines(self._lines(theme))
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


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    state = TodoState()
    board = TodoBoard(state)

    def _rebuild(_event: Any, ctx: ExtensionContext) -> None:
        state.rebuild(ctx.branch_entries)
        board.sync(ctx)

    tau.on("session_start", _rebuild)
    tau.on("session_tree", _rebuild)

    def _on_tui_ready(_event: Any, ctx: ExtensionContext) -> None:
        board.sync(ctx)

    tau.on("tui_ready", _on_tui_ready)

    def _on_shutdown(_event: Any, ctx: ExtensionContext) -> None:
        board.hide(ctx)

    tau.on("session_shutdown", _on_shutdown)

    def _inject_todo_state(_event: Any, _ctx: ExtensionContext) -> Any:
        """Re-assert the live todo list into context on every turn.

        Old `todo` tool calls/results fall out of view once compaction (or
        just enough turns) pushes them out of the kept window, and the
        board is TUI-only — the LLM otherwise has no way to recover the
        list. Injecting it fresh, ephemerally, each turn means it never
        depends on surviving compaction or being noticed by the
        summarizer: it's simply re-told every time, so it's never stale.
        """
        from tau.hooks.engine import ContextEventResult
        from tau.message.types import UserMessage

        remaining = state.remaining()
        if not remaining:
            return None
        lines = "\n".join(f"- #{i.id} [{i.status}] {i.subject}" for i in remaining)
        reminder = UserMessage.from_text(
            f"[Todo list reminder — not from the user, injected each turn]\n{lines}"
        )
        return ContextEventResult(ephemeral_messages=[reminder])

    tau.on("context", _inject_todo_state)

    tau.register_tool(TodoTool(state, tau._runtime_ref, on_mutate=board.sync))

    async def cmd_todos(ctx: ExtensionContext, _args: list[str]) -> None:
        ui = ctx.ui
        if ui is None:
            return
        if not state.items:
            ui.notify("No todos yet. Ask the agent to add some!")
            return
        sections = [
            ("Pending", "☐", state.list("pending")),
            ("In Progress", "■", state.list("in_progress")),
            ("Done", "✓", state.list("done")),
            ("Failed", "✗", state.list("failed")),
        ]
        header = " · ".join(
            f"{len(items)} {label.lower()}" for label, _, items in sections if items
        )
        lines = [header]
        for label, glyph, items in sections:
            if not items:
                continue
            lines.append(f"── {label} ──")
            lines += [f"  {glyph} {i.line()}" for i in items]
        ui.notify("\n".join(lines))

    tau.register_command(
        "todos",
        "Show the current todo list",
        cmd_todos,
        requires_idle=False,
    )
