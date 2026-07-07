from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from tau.extensions.loader import _RuntimeRef
from tau.tool.types import ToolInvocation

# Add the builtin ask_user extension directory to sys.path to allow imports under test.
sys.path.insert(
    0,
    str(Path(__file__).parent.parent / "tau" / "builtins" / "extensions" / "ask_user"),
)

from schema import AskUserParams
from tool import AskUserTool


def test_ask_user_tool_initialization() -> None:
    runtime_ref = _RuntimeRef()
    tool = AskUserTool(runtime_ref)
    assert tool.name == "ask_user"
    assert tool.schema == AskUserParams


def test_ask_user_tool_requires_tui() -> None:
    async def exercise() -> None:
        runtime = SimpleNamespace(
            session_manager=None,
            agent=None,
            settings_manager=None,
            _layout=None,
        )
        runtime_ref = _RuntimeRef()
        runtime_ref.runtime = runtime

        tool = AskUserTool(runtime_ref)
        invocation = ToolInvocation(
            id="call_1",
            name="ask_user",
            cwd=Path.cwd(),
            params={"questions": [{"question": "Should we proceed?"}]},
        )
        result = await tool.execute(invocation)
        assert result.is_error
        assert "requires an interactive TUI session" in result.content

    asyncio.run(exercise())
