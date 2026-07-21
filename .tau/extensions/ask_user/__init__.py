from __future__ import annotations

from .tool import AskUserTool


def register(tau) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return
    tau.register_tool(AskUserTool(tau._runtime_ref))
