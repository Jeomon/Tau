"""Reuses the subagent extension's agent discovery (same presets: scout,
worker, reviewer, ...), loaded by file path so this extension has no
import-order dependency on the subagent extension having loaded first.

Named agent_discovery.py, not agents.py: both this extension's __init__.py
and subagent/__init__.py do `sys.path.insert(0, own_dir)` then a plain
`import`. A file named agents.py here would collide with subagent/agents.py
in sys.modules — whichever extension's __init__.py runs first (alphabetical
discovery order: subagent < workflow) wins the "agents" name, and the other
silently imports the wrong module. Caused a real bug: workflow ended up
calling subagent's discover_agents(), which returns a (list, Path | None)
tuple instead of a flat list, so agent lookup iterated over that tuple and
crashed on its second element.
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
