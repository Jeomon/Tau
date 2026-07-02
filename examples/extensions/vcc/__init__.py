"""vcc — algorithmic conversation compaction for tau.

A port of vcc. Instead of asking an LLM to summarize the conversation before
it is compacted, vcc builds a deterministic summary by extraction and formatting
(session goal, files & changes, commits, outstanding context, user preferences,
plus a rolling brief transcript). It hooks tau's ``before_compaction`` event and
returns its own ``CompactionResult``.

Capabilities:
  - ``before_compaction`` handler — replaces the default LLM summary.
  - ``/vcc`` command          — compact on demand with the algorithmic summarizer.
  - ``/vcc-recall`` command   — search compacted history, feed results to the agent.
  - ``vcc_recall`` tool       — lossless history search the agent can call itself.

Gating (see settings): by default vcc only runs for an explicit ``/vcc``; enable
``override_default_compaction`` to let it handle every compaction path.
"""

from __future__ import annotations

from typing import Any

from .commands import register_commands
from .hook import register_before_compaction_hook
from .recall import VccRecallTool
from .state import STATE, VccConfig


def _load_config(raw: dict[str, Any] | None) -> VccConfig:
    raw = raw or {}
    return VccConfig(
        override_default_compaction=bool(raw.get("override_default_compaction", False)),
        debug=bool(raw.get("debug", False)),
    )


def register(tau: Any) -> None:
    # Refresh shared config on every (re)load so settings changes take effect live.
    STATE.config = _load_config(tau.config)

    register_before_compaction_hook(tau)
    register_commands(tau)
    tau.register_tool(VccRecallTool(tau._runtime_ref))
