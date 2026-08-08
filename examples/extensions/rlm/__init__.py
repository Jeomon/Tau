"""Recursive Language Model querying over text too large to read.

Registers ``rlm_query``. See ``tool.py`` for what the tool does and
``repl.py`` for the environment the model drives.
"""

from __future__ import annotations

from .tool import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_SUB_CALL_BUDGET,
    RLMTool,
)
from tau.extensions import ExtensionAPI

__all__ = ["register"]


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    tau.register_tool(
        RLMTool(
            max_iterations=int(config.get("max_iterations", DEFAULT_MAX_ITERATIONS)),
            sub_call_budget=int(config.get("sub_call_budget", DEFAULT_SUB_CALL_BUDGET)),
        )
    )
