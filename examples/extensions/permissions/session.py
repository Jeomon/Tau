"""In-memory approvals granted during this session.

Session grants are deliberately **never written to disk**. Two reasons:

* The agent has file tools. Anything persisted under the project is something
  it can read to discover what it is allowed to do, and potentially edit.
* A grant made in the heat of one task should not silently outlive it. Losing
  them on restart is the feature, not a limitation.

Project- and global-scoped grants *are* persisted, but they go through
:mod:`config` like any other rule, so they are visible in a file the user
chose to edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rules import Rule, match_any


@dataclass
class SessionGrants:
    """Approvals that live only as long as the process."""

    _rules: list[Rule] = field(default_factory=list)

    def grant(self, surface: str, pattern: str) -> None:
        """Allow ``pattern`` on ``surface`` for the rest of the session."""
        self._rules.append(Rule(pattern=f"{surface}\x00{pattern}", state="allow", origin="session"))

    def allows(self, surface: str, values: list[str]) -> Rule | None:
        """Return the grant covering ``values``, if one exists."""
        scoped = [f"{surface}\x00{v}" for v in values]
        for rule in reversed(self._rules):
            if match_any(rule.pattern, scoped):
                return rule
        return None

    def clear(self) -> None:
        self._rules.clear()

    def describe(self) -> list[str]:
        """Human-readable grants, for ``/permissions``."""
        out: list[str] = []
        for rule in self._rules:
            surface, _, pattern = rule.pattern.partition("\x00")
            out.append(f"{surface}: {pattern}")
        return out

    def __len__(self) -> int:
        return len(self._rules)
