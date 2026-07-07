"""subagent extension — delegate tasks to specialized subagents with isolated context.

Ported from the pi coding agent's reference subagent extension: a `subagent`
tool that spawns a separate `tau` process per invocation (single, parallel, or
chained), so each delegated task gets its own context window instead of
consuming the parent conversation's.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))

from subagent_tool import SubagentTool  # noqa: E402

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    tau.register_tool(SubagentTool(tau._runtime_ref))
