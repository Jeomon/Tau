from __future__ import annotations

import sys
from pathlib import Path

# Add this directory to sys.path to allow easy resolution of schema, component, and tool.
sys.path.insert(0, str(Path(__file__).parent))

from tool import AskUserTool


def register(tau) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return
    tau.register_tool(AskUserTool(tau._runtime_ref))
