"""todo extension — a simple todo list tool for tracking multi-step work.

Ported from the pi coding agent's reference todo extension: a single `todo`
tool with list/add/toggle/clear actions, state rebuilt by replaying the
session's custom entries (so it naturally follows branch/fork navigation),
plus a `/todos` command for a quick look.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).parent))

from todo_tool import TodoState, TodoTool  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    state = TodoState()

    def _rebuild(_event: Any, ctx: ExtensionContext) -> None:
        state.rebuild(ctx.branch_entries)

    tau.on("session_start", _rebuild)
    tau.on("session_tree", _rebuild)

    tau.register_tool(TodoTool(state, tau._runtime_ref))

    async def cmd_todos(ctx: ExtensionContext, _args: list[str]) -> None:
        ui = ctx.ui
        if ui is not None:
            ui.notify(state.render())

    tau.register_command(
        "todos",
        "Show the current todo list",
        cmd_todos,
        requires_idle=False,
    )
