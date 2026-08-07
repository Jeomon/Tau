"""Append-only decision log.

The log answers one question that is otherwise unanswerable after the fact:
*why* was this blocked? Recording the matched pattern and its origin turns
"the agent said it was denied" into "the project-scope rule ``**/.env`` denied
it", which is the difference between a policy you can debug and one you delete
in frustration.

Entries are JSON Lines. The log lives next to the config and is covered by the
same self-protection rule, so the agent cannot quietly edit its own history.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .rules import Decision

_log = logging.getLogger(__name__)

LOG_RELATIVE = Path(".tau/extensions/permissions/decisions.log")

#: Parameters never written to the log, because their values are the payload
#: rather than the target — logging them would copy file contents into a file
#: that is easier to read than the original.
_REDACT = frozenset({"content", "new_content"})

_MAX_VALUE = 200


def _safe_params(params: dict) -> dict:
    out: dict[str, object] = {}
    for key, value in params.items():
        if key in _REDACT:
            out[key] = f"<{len(str(value))} chars redacted>"
        elif isinstance(value, str) and len(value) > _MAX_VALUE:
            out[key] = value[:_MAX_VALUE] + "…"
        else:
            out[key] = value
    return out


class DecisionLog:
    """Writes one JSON object per decision. Never raises."""

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self._path = root / LOG_RELATIVE
        self._enabled = enabled

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        tool_name: str,
        params: dict,
        decision: Decision,
        *,
        prompted: bool = False,
        outcome: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool_name,
            "params": _safe_params(params),
            "state": decision.state,
            "surface": decision.surface,
            "target": decision.target,
            "pattern": decision.matched_pattern,
            "origin": decision.origin,
            "reason": decision.reason,
            "command_context": decision.command_context,
            "prompted": prompted,
            "outcome": outcome,
        }
        try:
            # 0600/0700, matching how tau stores auth.json and the telemetry
            # marker. The log names every file touched and every command run,
            # which is a more revealing record than most of what it sits next
            # to; it was being written world-readable while a file holding a
            # version string was not. Applied only on creation, to keep a
            # chmod off the path of every decision.
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            new_file = not self._path.exists()
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
            if new_file:
                self._path.chmod(0o600)
        except Exception as exc:  # noqa: BLE001 - see below; never narrow this
            # Logging is observability, not enforcement. A full disk must not
            # turn into a denied tool call — nor, far worse, into an allowed
            # one: this runs *after* the decision is made, and an exception
            # escaping here propagates out of the tool_call handler, which the
            # host treats as "no objection" and executes the call the user just
            # denied. Catching only OSError left that open for anything
            # json.dumps could raise on an odd parameter value.
            _log.warning("permissions: cannot write decision log: %s", exc)

    def tail(self, count: int = 20) -> list[dict]:
        """Read back the most recent entries, for ``/permissions log``."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in lines[-count:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
