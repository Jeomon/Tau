"""Reuses the subagent extension's agent discovery (same presets: scout,
worker, reviewer, ...).

Loaded by file path, not imported: it lives in a *different* extension, so a
relative import cannot reach it and a bare one would depend on the subagent
extension having been loaded first. The explicit module name keeps it out of
the shared `sys.modules` namespace, and the target module must stay
sibling-import-free for a path load to work at all.

(This file was once named to dodge a `sys.modules` collision with
subagent/agents.py — back when both extensions put their own directory on
`sys.path` and imported siblings by bare name. Extensions now use relative
imports, so that hazard is gone; the name simply stayed.)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_MODULE_NAME = "_tau_workflow_subagent_agents"
_MODULE_PATH = Path(__file__).parent.parent / "subagent" / "agents.py"
_cached: Any = None


def _module() -> Any:
    global _cached
    if _cached is None:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module  # dataclass needs this registered before exec
        spec.loader.exec_module(module)
        _cached = module
    return _cached


def discover_agents(cwd: Path) -> list[Any]:
    result = _module().discover_agents(cwd)
    return result[0]
