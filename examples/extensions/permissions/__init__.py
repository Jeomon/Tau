"""Permission gate for Tau.

Intercepts every tool call before it runs, resolves it against a layered
policy, and allows, asks, or denies. See ``README.md`` in this directory for
the configuration format and the design rationale.

The whole extension hangs off one hook::

    @tau.on("tool_call") -> ToolCallEventResult(block=..., reason=...)

which is the only point in Tau that can stop a tool from executing.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any

from tau.hooks import ToolCallEventResult, ToolResultEventResult

from .config import Policy, load_policy
from .log import DecisionLog
from .prompt import ask
from .resolver import AccessIntent, Resolver, denied_tools, find_grant_pattern
from .rules import Decision
from .session import SessionGrants

_log = logging.getLogger(__name__)


class PermissionGate:
    """Holds the policy, session grants, and decision log for one session."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.grants = SessionGrants()
        self.policy: Policy = load_policy(cwd, trusted=False)
        self.log = DecisionLog(cwd, enabled=self.policy.settings.log_decisions)
        self.trusted = False
        # Decisions awaiting their tool result, keyed by tool call id. A gated
        # call is decided in `tool_call` and finishes in `tool_result`, and only
        # the second knows whether the tool actually succeeded. Bounded because
        # a blocked call never reaches `tool_result` — see `take_decision`.
        self._pending: OrderedDict[str, Decision] = OrderedDict()

    #: Decisions kept while their tool runs. Calls are gated one at a time, so
    #: this holds one entry in practice; the cap only bounds the pathological
    #: case where results never arrive.
    _PENDING_LIMIT = 64

    def remember_decision(self, tool_call_id: str, decision: Decision) -> None:
        """Hold a decision until its tool result arrives."""
        if not tool_call_id:
            return
        self._pending[tool_call_id] = decision
        while len(self._pending) > self._PENDING_LIMIT:
            self._pending.popitem(last=False)

    def take_decision(self, tool_call_id: str) -> Decision | None:
        """Retrieve and forget the decision for a finished call."""
        return self._pending.pop(tool_call_id, None)

    def reload(self, *, trusted: bool) -> None:
        """Re-read the policy from disk, keeping this gate's session grants.

        Grants survive a policy reload but not an *extension* reload, where
        ``register()`` builds a whole new gate — see the note next to the
        ``extension_reloaded`` subscription.
        """
        self.trusted = trusted
        self.policy = load_policy(self.cwd, trusted=trusted)
        self.log = DecisionLog(self.cwd, enabled=self.policy.settings.log_decisions)

    @property
    def resolver(self) -> Resolver:
        # Rebuilt per call so a `/reload` between turns takes effect without
        # any cache to invalidate.
        return Resolver(self.policy, self.grants, self.cwd)

    async def decide(self, tool_name: str, params: dict, ui: Any, registry: Any = None) -> Decision:
        """Resolve one call, prompting when the policy says to ask."""
        decision = self.resolver.resolve(AccessIntent(tool_name, params, self.cwd))

        if decision.state != "ask":
            self.log.record(tool_name, params, decision)
            return decision

        if ui is None:
            # Nothing can be asked here and nothing will change later in the
            # run, so apply the configured default rather than hanging.
            fallback = self.policy.settings.headless_default
            resolved = Decision(
                state=fallback,
                surface=decision.surface,
                target=decision.target,
                matched_pattern=decision.matched_pattern,
                origin=decision.origin,
                reason=(decision.reason or "No interactive surface available to request approval."),
                command_context=decision.command_context,
            )
            self.log.record(tool_name, params, resolved, prompted=False, outcome="headless")
            return resolved

        outcome, pattern = await ask(
            ui,
            decision,
            timeout_seconds=self.policy.settings.prompt_timeout_seconds,
            suggestion=find_grant_pattern(decision),
            params=params,
            cwd=self.cwd,
            registry=registry,
        )

        if outcome == "allow_session" and pattern:
            self.grants.grant(decision.surface, pattern)

        granted = outcome in ("allow_once", "allow_session")
        resolved = Decision(
            state="allow" if granted else "deny",
            surface=decision.surface,
            target=decision.target,
            matched_pattern=decision.matched_pattern,
            origin="session" if granted else decision.origin,
            reason=None if granted else "Denied by the user.",
            command_context=decision.command_context,
        )
        self.log.record(tool_name, params, resolved, prompted=True, outcome=outcome)
        return resolved


def _audit(decision: Decision, *, execution: str) -> dict[str, Any]:
    """The decision as fields, for the tool result's metadata.

    The same shape whether the call was permitted or blocked, so a transcript
    can be queried without caring which happened — ``execution`` carries that.
    ``_denial_message`` states the reason in prose for the model to read; this
    is the machine-readable half, which prose is a poor substitute for.
    """
    return {
        "state": decision.state,
        "surface": decision.surface,
        "pattern": decision.matched_pattern,
        "origin": decision.origin,
        "execution": execution,
    }


def _denial_message(decision: Decision) -> str:
    """What the model is told. Specific enough to adapt to, not to probe with."""
    parts = [decision.reason or f"Denied by permission policy ({decision.surface})."]
    if decision.matched_pattern:
        parts.append(f"Matched rule: {decision.matched_pattern}")
    if decision.command_context:
        parts.append(f"Context: {decision.command_context.replace('_', ' ')}")
    parts.append("Do not retry this call; ask the user if you believe it should be permitted.")
    return " ".join(parts)


def register(tau: Any) -> None:
    gate = PermissionGate(Path(tau.cwd))

    async def _load(event: Any, ctx: Any) -> None:
        # Trust is only resolvable once the runtime exists, and it decides
        # whether the project-scope config is allowed to load at all.
        trusted = False
        try:
            trusted = bool(await ctx.is_project_trusted())
        except Exception:  # noqa: BLE001 - untrusted is the safe assumption
            _log.debug("permissions: trust unresolved; treating project as untrusted")
        gate.reload(trusted=trusted)

        if gate.policy.settings.hide_denied_tools:
            hidden = denied_tools(gate.policy)
            if hidden:
                current = ctx.get_system_prompt_options().get("tools") or []
                keep = [name for name in current if name not in hidden]
                # An empty list means "re-enable everything" to
                # set_active_tools, so never send one — that would undo the
                # very restriction being applied.
                if keep:
                    tau.set_active_tools(keep)

        if gate.policy.invalid_scopes and ctx.ui is not None:
            ctx.ui.notify(
                "permissions: ignoring invalid config in "
                + ", ".join(gate.policy.invalid_scopes)
                + " scope (allow rules clamped to ask)",
                "warning",
            )

    # runtime_ready fires once at startup; extension_reloaded fires on every
    # /reload, where register() has just rebuilt `gate` from scratch. Both must
    # load the policy or an edited config.json would sit on disk unread — and
    # after a reload the gate would be running on built-in defaults while
    # `/permissions` happily reported the file's contents.
    #
    # Session grants are NOT restored here, and that is deliberate: `gate` is a
    # fresh object, so approvals granted before the reload are gone and the
    # user is asked again. Re-prompting is the fail-safe direction, and the
    # alternative — persisting grants somewhere durable — would put the agent's
    # own permissions in a file the agent can read.
    tau.on("runtime_ready", _load)
    tau.on("extension_reloaded", _load)

    @tau.on("tool_call")
    async def _gate(event: Any, ctx: Any) -> ToolCallEventResult | None:
        # The tool registry lets the prompt preview a write/edit with that
        # tool's own renderer, so the approval view matches the result view.
        registry = getattr(getattr(ctx, "_runtime", None), "tool_registry", None)
        decision = await gate.decide(event.tool_name, event.input, ctx.ui, registry)
        if decision.state == "allow":
            gate.remember_decision(event.tool_call_id, decision)
            return None
        return ToolCallEventResult(
            block=True,
            reason=_denial_message(decision),
            # A denial gets the same fields an allow does. Without this the
            # session recorded only the host's `blocked` flag plus the reason as
            # prose, so the one outcome most worth auditing was the one stored
            # least precisely.
            metadata={"_permission": _audit(decision, execution="blocked")},
        )

    @tau.on("tool_result")
    async def _annotate(event: Any, ctx: Any) -> ToolResultEventResult | None:
        """Record what the permitted call actually did.

        The decision log answers "was this allowed, and by which rule". Until
        now nothing answered "and did it then work" — the log is written before
        the tool runs, so an approval that ended in a failure looked identical
        to one that succeeded. Only `tool_result` knows.

        The same detail goes onto the tool result's metadata, which the session
        persists, so a transcript carries the reason a call was permitted next
        to the call itself rather than only in a separate file.

        Blocked calls never arrive here: the host turns a `block` into a tool
        result directly and skips execution, so `_pending` only ever holds
        allows, and each is removed as it completes.
        """
        decision = gate.take_decision(event.tool_call_id)
        if decision is None:
            return None

        outcome = "error" if event.is_error else "ok"
        gate.log.record(
            event.tool_name,
            event.input,
            decision,
            prompted=decision.origin == "session",
            outcome=f"executed:{outcome}",
        )
        return ToolResultEventResult(metadata={"_permission": _audit(decision, execution=outcome)})

    async def _cmd(ctx: Any, args: list[str]) -> None:
        if ctx.ui is None:
            return
        if args and args[0] == "log":
            entries = gate.log.tail(15)
            if not entries:
                ctx.ui.notify("permissions: no decisions recorded yet", "info")
                return
            ctx.ui.notify(
                [
                    f"{e.get('state'):5} {e.get('tool'):9} {e.get('target', '')}"
                    f"  [{e.get('pattern')}]"
                    for e in entries
                ],
                "info",
            )
            return

        if args and args[0] == "reload":
            trusted = bool(await ctx.is_project_trusted())
            gate.reload(trusted=trusted)
            ctx.ui.notify("permissions: policy reloaded", "success")
            return

        if args and args[0] == "revoke":
            count = len(gate.grants)
            gate.grants.clear()
            ctx.ui.notify(f"permissions: cleared {count} session grant(s)", "success")
            return

        lines = [
            f"project trusted: {gate.trusted}",
            f"default: {gate.policy.default_state}",
            f"tools: {', '.join(sorted(gate.policy.known_tools())) or '(none)'}",
            f"path rules: {len(gate.policy.path_rules)}",
            f"command rules: {len(gate.policy.tool_rules.get('terminal', []))}",
            f"headless default: {gate.policy.settings.headless_default}",
            f"prompt timeout: {gate.policy.settings.prompt_timeout_seconds}s",
            f"session grants: {len(gate.grants)}",
        ]
        lines.extend(f"  granted: {g}" for g in gate.grants.describe())
        if gate.policy.invalid_scopes:
            lines.append(f"invalid scopes: {', '.join(gate.policy.invalid_scopes)}")
        ctx.ui.notify(lines, "info")

    tau.register_command(
        "permissions",
        "Show permission policy, session grants, and recent decisions",
        _cmd,
        argument_hint="[log|reload|revoke]",
    )
