from __future__ import annotations

import json
import logging
from typing import Any

from .core.summarize import compile_summary
from .state import STATE

_log = logging.getLogger("vcc")


def _write_debug(data: dict[str, Any]) -> None:
    try:
        with open("/tmp/vcc-debug.json", "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
    except OSError:
        pass


def register_before_compaction_hook(tau: Any) -> None:
    """Register a ``before_compaction`` handler that replaces tau's LLM summary
    with an algorithmic vcc summary.

    Gating:
      - Always runs for an explicit ``/vcc`` (one-shot ``force_next`` flag).
      - Otherwise runs only when ``override_default_compaction`` is enabled.
    Any failure returns ``None`` so tau falls back to its default summarizer.
    """
    from tau.hooks.engine import BeforeCompactionResult
    from tau.session.compaction import CompactionResult

    @tau.on("before_compaction")
    async def _on_before_compaction(event: Any, ctx: Any) -> Any:
        force = STATE.force_next
        STATE.force_next = False

        if not force and not STATE.config.override_default_compaction:
            return None

        prep = getattr(event, "preparation", None)
        if prep is None:
            return None

        try:
            messages = list(prep.messages_to_summarize)
            prefix = getattr(prep, "turn_prefix_messages", None)
            if prefix:
                # Everything before first_kept_entry_id is dropped and replaced by
                # our summary, so fold the split-turn prefix into the same input.
                messages = messages + list(prefix)

            summary = compile_summary(messages, previous_summary=prep.previous_summary)
            if not summary:
                return None

            details = {
                "compactor": "vcc",
                "version": 1,
                "reason": str(getattr(event, "reason", "")),
                "manual": bool(getattr(event, "manual", False)),
                "forced": force,
                "source_message_count": len(messages),
                "previous_summary_used": bool(prep.previous_summary),
            }

            if STATE.config.debug:
                _write_debug({**details, "summary_preview": summary[:800]})

            return BeforeCompactionResult(
                compaction=CompactionResult(
                    summary=summary,
                    first_kept_entry_id=prep.first_kept_entry_id,
                    tokens_before=prep.tokens_before,
                    details=details,
                )
            )
        except Exception:
            _log.exception("vcc before_compaction failed; falling back to default")
            return None
