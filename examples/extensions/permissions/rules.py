"""Permission states, rules, and pattern matching.

Two precedence rules govern everything in this package, and they are
deliberately different from each other:

* **Within** a single pattern map, the *last* matching rule wins. Broad
  catch-alls go first, specific overrides after — the same way a ``.gitignore``
  reads.
* **Across** layers (path, external directory, tool, command), the *most
  restrictive* decision wins, so a permissive rule in one layer can never
  loosen a stricter one in another.

Keeping the two straight is the difference between a policy that does what it
looks like it does and one that quietly grants more than intended.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PermissionState = Literal["allow", "ask", "deny"]

#: Ordering used by :func:`most_restrictive`. Higher wins.
STATE_SEVERITY: dict[PermissionState, int] = {"allow": 0, "ask": 1, "deny": 2}

#: Where a winning rule came from, for the decision log and the prompt.
RuleOrigin = Literal["default", "global", "project", "session", "builtin"]


def is_permission_state(value: object) -> bool:
    """True when ``value`` is one of the three permission states."""
    return value in ("allow", "ask", "deny")


def most_restrictive(states: list[PermissionState]) -> PermissionState:
    """Fold layer decisions together. ``deny`` beats ``ask`` beats ``allow``."""
    if not states:
        return "allow"
    return max(states, key=lambda s: STATE_SEVERITY[s])


@dataclass(frozen=True)
class Rule:
    """One pattern-to-state mapping, tagged with where it was defined."""

    pattern: str
    state: PermissionState
    origin: RuleOrigin = "default"
    reason: str | None = None

    def matches(self, value: str) -> bool:
        return match_pattern(self.pattern, value)


@dataclass(frozen=True)
class Decision:
    """The resolved answer for one access intent."""

    state: PermissionState
    surface: str
    target: str = ""
    matched_pattern: str | None = None
    origin: RuleOrigin = "default"
    reason: str | None = None
    #: Set when the offending command ran inside a substitution or subshell.
    command_context: str | None = None

    @property
    def allowed(self) -> bool:
        return self.state == "allow"


# ── Pattern matching ─────────────────────────────────────────────────────────

_CACHE: dict[str, re.Pattern[str]] = {}


def _translate(pattern: str) -> re.Pattern[str]:
    """Compile a glob to a regex.

    Semantics, chosen to match how people actually write these patterns:

    * ``**`` always crosses ``/``.
    * ``*`` normally stops at ``/`` — ``src/*`` is one level deep.
    * A **trailing** ``*`` is greedy and crosses ``/``, so ``~/.cargo/*``
      covers the whole tree without needing ``**``. This is the single most
      surprising rule, and the one that stops a policy author from accidentally
      writing a boundary that only protects the top directory.
    * ``?`` matches exactly one non-separator character.
    """
    out: list[str] = ["\\A"]
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                # Swallow a separator right after ** so `a/**/b` matches `a/b`.
                if i < n and pattern[i] == "/":
                    i += 1
                continue
            if i + 1 == n:  # trailing star — greedy, crosses separators
                out.append(".*")
            else:
                out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(ch))
        i += 1
    out.append("\\Z")
    return re.compile("".join(out))


def match_pattern(pattern: str, value: str) -> bool:
    """True when ``value`` matches the glob ``pattern``."""
    if pattern == "*" or pattern == "**":
        return True
    compiled = _CACHE.get(pattern)
    if compiled is None:
        compiled = _translate(pattern)
        _CACHE[pattern] = compiled
    return compiled.match(value) is not None


def match_any(pattern: str, values: list[str]) -> bool:
    """True when the pattern matches *any* alias of the target.

    Callers pass every spelling of a path (as written, absolute, symlink
    resolved) so a rule cannot be dodged by choosing a different spelling.
    """
    return any(match_pattern(pattern, v) for v in values)


def resolve_rules(rules: list[Rule], values: list[str]) -> Rule | None:
    """Return the winning rule for ``values``, or ``None`` when none match.

    Last match wins, so callers build the list in declaration order.
    """
    winner: Rule | None = None
    for rule in rules:
        if match_any(rule.pattern, values):
            winner = rule
    return winner
