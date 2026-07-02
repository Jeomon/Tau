from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VccConfig:
    """Type-safe schema for the vcc extension's settings.json config."""

    # When False, vcc only runs for an explicit /vcc; /compact and auto-threshold
    # compaction fall through to tau's default (LLM) summarizer. When True, vcc
    # handles every compaction path.
    override_default_compaction: bool = False
    # When True, each compaction writes diagnostics to /tmp/vcc-debug.json.
    debug: bool = False


class _State:
    """Process-wide vcc state that must survive extension reloads.

    The ``force_next`` flag lets the ``/vcc`` command request a one-shot
    algorithmic compaction even when ``override_default_compaction`` is off:
    the command sets it, then triggers compaction, and the ``before_compaction``
    handler consumes it.
    """

    __slots__ = ("config", "force_next")

    def __init__(self) -> None:
        self.config = VccConfig()
        self.force_next = False


# Single shared instance (module is imported once under a stable name).
STATE = _State()
