"""The single decision point.

Every gate feeds one :meth:`Resolver.resolve` call. That is deliberate: with a
method per surface, a test can stub one and forget another, and adding a fifth
surface silently widens the interface. One entry point means one place to reason
about precedence and one place a test can lie about.

Four layers contribute a state each, and the **most restrictive wins**:

=====================  =====================================================
Layer                  Question it answers
=====================  =====================================================
``path``               Is this file off-limits to every tool?
``external_directory`` Does this reach outside the project?
``tool``               May this role use this tool at all?
``command``            Is each decomposed shell unit permitted?
=====================  =====================================================

Because the fold is most-restrictive, a permissive rule in one layer can never
loosen a stricter one in another — an ``allow`` on ``path`` cannot punch through
an ``external_directory: ask`` boundary.

Two clamps sit on top, and both fail closed:

* An **unresolvable** command (unparseable, or reached through ``xargs``/``$VAR``)
  can never resolve to ``allow``. The best it gets is ``ask``.
* An internal error resolves to ``deny``. A gate that crashes must not become a
  gate that permits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .command import decompose
from .config import Policy
from .paths import AccessPath, extract_path
from .rules import (
    Decision,
    PermissionState,
    most_restrictive,
    resolve_rules,
)
from .session import SessionGrants

_log = logging.getLogger(__name__)

#: Tools whose invocation writes to the path it names.
WRITE_TOOLS = frozenset({"write", "edit"})

#: Tools whose rule map holds shell-command patterns rather than tool-name or
#: path patterns. ``_command_layer`` owns those rules exclusively.
COMMAND_TOOLS = frozenset({"terminal"})


@dataclass(frozen=True)
class AccessIntent:
    """One tool call awaiting a decision."""

    tool_name: str
    params: dict
    cwd: Path


@dataclass
class _Layer:
    """One layer's contribution, kept so the winner can be explained."""

    state: PermissionState
    surface: str
    target: str = ""
    pattern: str | None = None
    origin: str = "default"
    reason: str | None = None
    command_context: str | None = None


class Resolver:
    """Answers access intents against a policy plus this session's grants."""

    def __init__(self, policy: Policy, grants: SessionGrants, cwd: Path) -> None:
        self._policy = policy
        self._grants = grants
        self._cwd = cwd

    # ── Public surface ───────────────────────────────────────────────────────

    def resolve(self, intent: AccessIntent) -> Decision:
        """Decide one tool call. Never raises."""
        try:
            return self._resolve(intent)
        except Exception:  # noqa: BLE001 - fail closed, never fail open
            _log.exception("permissions: gate error on %s", intent.tool_name)
            return Decision(
                state="deny",
                surface="gate_error",
                target=intent.tool_name,
                reason="Permission gate failed; denying rather than guessing.",
                origin="default",
            )

    # ── Layers ───────────────────────────────────────────────────────────────

    def _resolve(self, intent: AccessIntent) -> Decision:
        layers: list[_Layer] = []
        raw_path, is_write = extract_path(intent.tool_name, intent.params)
        access: AccessPath | None = None

        if raw_path is not None:
            access = AccessPath.build(raw_path, self._cwd)

            # Self-protection precedes every configurable rule: an agent that can
            # rewrite the policy has no policy.
            if (is_write or intent.tool_name in WRITE_TOOLS) and access.is_self_protected(
                self._cwd
            ):
                return Decision(
                    state="deny",
                    surface="self_protection",
                    target=access.absolute,
                    reason="This file is the permission extension's own configuration.",
                    origin="builtin",
                )

            layers.append(self._path_layer(access))
            external = self._external_layer(access)
            if external is not None:
                layers.append(external)

        layers.append(self._tool_layer(intent, access))

        unresolvable = False
        if intent.tool_name == "terminal":
            command_layer, unresolvable = self._command_layer(intent)
            if command_layer is not None:
                layers.append(command_layer)

        winner = self._fold(layers)

        # A grant only ever loosens an `ask`. It cannot overturn a deny — that
        # would let one hurried approval defeat a policy the user wrote down.
        #
        # The values tested have to match how the grant was written, which
        # differs per surface: `find_grant_pattern` suggests `<parent>/*` for a
        # path and `<program>*` for a command, and falls back to the decision
        # target — the bare tool name — on the tool surface. Testing path
        # spellings alone meant a grant could only ever match when the winning
        # surface was `path` or `external_directory`; `tool` and `command`
        # grants were recorded and then never consulted, so "Allow for this
        # session" silently did nothing and re-prompted on every call.
        # `command` is deliberately absent here: it is applied per unit in
        # `_command_layer`, because a whole-command lookup would let one
        # granted segment carry the others.
        if winner.state == "ask":
            values: list[str] | None = None
            if winner.surface in ("path", "external_directory") and access is not None:
                values = access.match_values()
            elif winner.surface == "tool":
                values = [intent.tool_name]

            grant = self._grants.allows(winner.surface, values) if values else None
            if grant is not None:
                return Decision(
                    state="allow",
                    surface=winner.surface,
                    target=winner.target,
                    matched_pattern=grant.pattern.split("\x00", 1)[-1],
                    origin="session",
                )

        if unresolvable and winner.state == "allow":
            return Decision(
                state="ask",
                surface="command",
                target=winner.target,
                reason="Cannot determine what this command runs.",
                origin=winner.origin,  # type: ignore[arg-type]
                command_context=winner.command_context,
            )

        return Decision(
            state=winner.state,
            surface=winner.surface,
            target=winner.target,
            matched_pattern=winner.pattern,
            origin=winner.origin,  # type: ignore[arg-type]
            reason=winner.reason,
            command_context=winner.command_context,
        )

    def _path_layer(self, access: AccessPath) -> _Layer:
        """Cross-cutting file rules, applied to every tool that names a path."""
        rule = resolve_rules(self._policy.path_rules, access.match_values())
        if rule is None:
            return _Layer(state="allow", surface="path", target=access.absolute)
        return _Layer(
            state=rule.state,
            surface="path",
            target=access.absolute,
            pattern=rule.pattern,
            origin=rule.origin,
            reason=rule.reason,
        )

    def _external_layer(self, access: AccessPath) -> _Layer | None:
        """The project boundary. Only consulted when the path leaves it."""
        if not access.escapes(self._cwd):
            return None
        rule = resolve_rules(self._policy.external_rules, access.match_values())
        if rule is None:
            return _Layer(
                state="ask",
                surface="external_directory",
                target=access.absolute,
                reason="Outside the project directory.",
            )
        return _Layer(
            state=rule.state,
            surface="external_directory",
            target=access.absolute,
            pattern=rule.pattern,
            origin=rule.origin,
            reason=rule.reason or "Outside the project directory.",
        )

    def _tool_layer(self, intent: AccessIntent, access: AccessPath | None) -> _Layer:
        """May this tool be used, and on this target?"""
        name = intent.tool_name

        flat = self._policy.tool_states.get(name)
        if flat is not None:
            return _Layer(
                state=flat,
                surface="tool",
                target=name,
                pattern=name,
                origin="global",
                reason=self._policy.tool_reasons.get(name),
            )

        # For a tool with its own command layer, the rule map holds *command*
        # patterns. Matching them here against the literal tool name would let
        # a catch-all like `"*": "ask"` shadow every specific `allow` the author
        # wrote, so ownership of those rules belongs solely to `_command_layer`.
        rules = None if name in COMMAND_TOOLS else self._policy.tool_rules.get(name)
        if rules:
            values = access.match_values() if access is not None else [name]
            rule = resolve_rules(rules, values)
            if rule is not None:
                return _Layer(
                    state=rule.state,
                    surface="tool",
                    target=values[0],
                    pattern=rule.pattern,
                    origin=rule.origin,
                    reason=rule.reason,
                )

        return _Layer(
            state=self._policy.default_state,
            surface="tool",
            target=name,
            pattern="*",
        )

    def _command_layer(self, intent: AccessIntent) -> tuple[_Layer | None, bool]:
        """Gate every decomposed unit of a shell command.

        Returns ``(layer, unresolvable)``. The layer reflects the most
        restrictive unit, so one dangerous segment condemns the whole string —
        the shell would run them all.
        """
        command = intent.params.get("cmd")
        if not isinstance(command, str) or not command.strip():
            return None, False

        rules = self._policy.tool_rules.get("terminal") or []
        decomposition = decompose(command)
        unresolvable = not decomposition.parsed or decomposition.has_indirect

        worst: _Layer | None = None
        for unit in decomposition.units:
            rule = resolve_rules(rules, [unit.text])
            state: PermissionState = rule.state if rule is not None else "ask"
            pattern = rule.pattern if rule else None
            origin = rule.origin if rule else "default"
            reason = rule.reason if rule else None

            # Session grants are applied per unit, never to the command as a
            # whole. `SessionGrants.allows` matches if *any* value matches, so
            # testing every unit text at once would let a grant for one segment
            # carry the rest: `cd /safe && rm -rf /` decomposes into two units,
            # and a `cd*` grant would otherwise allow the whole string, which
            # the shell runs in full. Each unit must earn its own allow.
            if state == "ask":
                grant = self._grants.allows("command", [unit.text])
                if grant is not None:
                    state = "allow"
                    pattern = grant.pattern.split("\x00", 1)[-1]
                    origin = "session"
                    reason = None

            candidate = _Layer(
                state=state,
                surface="command",
                target=unit.text,
                pattern=pattern,
                origin=origin,
                reason=reason,
                command_context=unit.context,
            )
            if worst is None or _severity(candidate.state) > _severity(worst.state):
                worst = candidate

        return worst, unresolvable

    # ── Folding ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fold(layers: list[_Layer]) -> _Layer:
        """Pick the most restrictive layer, keeping its explanation."""
        if not layers:
            return _Layer(state="ask", surface="tool")
        state = most_restrictive([layer.state for layer in layers])
        for layer in layers:
            if layer.state == state:
                return layer
        return layers[0]


def _severity(state: PermissionState) -> int:
    from .rules import STATE_SEVERITY

    return STATE_SEVERITY[state]


def denied_tools(policy: Policy) -> set[str]:
    """Tools whose *only* possible outcome is deny, for pre-turn hiding.

    Conservative on purpose: a tool with pattern rules is not hidden, because
    some target might still be permitted, and hiding it would remove a
    capability the policy actually grants.
    """
    hidden: set[str] = set()
    for name, state in policy.tool_states.items():
        if state == "deny":
            hidden.add(name)
    return hidden


def find_grant_pattern(decision: Decision) -> str | None:
    """Suggest a reusable pattern for "always allow" on this decision.

    Offering only the exact target trains people to approve repeatedly; offering
    a whole directory or program is what they actually want.
    """
    if decision.surface == "command":
        program = decision.target.split(" ", 1)[0]
        return f"{program}*" if program else None
    if decision.surface in ("path", "external_directory"):
        parent = str(Path(decision.target).parent)
        return f"{parent}/*" if parent not in ("", "/") else None
    return None
