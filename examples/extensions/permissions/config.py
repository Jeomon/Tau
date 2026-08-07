"""Config loading, validation, and scope merging.

Two scopes are read, lowest priority first:

===========  ====================================================
Scope        Location
===========  ====================================================
``global``   ``~/.tau/extensions/permissions/config.json``
``project``  ``<cwd>/.tau/extensions/permissions/config.json``
===========  ====================================================

Rules from both are concatenated in that order, so a project rule that matches
the same target as a global one wins by being *later* (see ``rules.py`` on
last-match-wins). Scalars are simply overridden.

Two safety properties are enforced here rather than at the gates:

* **Project config only loads in a trusted directory.** Otherwise cloning a
  repository would hand it the ability to ship a policy that permits anything.
* **A malformed scope fails closed.** If a file exists but does not parse or
  validate, its own rules are dropped *and* every remaining ``allow`` in that
  scope is clamped to ``ask``. Silently continuing with a permissive default is
  how a typo becomes an incident.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rules import PermissionState, Rule, RuleOrigin, is_permission_state

_log = logging.getLogger(__name__)

CONFIG_RELATIVE = Path(".tau/extensions/permissions/config.json")

#: Keys inside ``permission`` that name a *layer* rather than a tool.
LAYER_KEYS = ("path", "external_directory")

#: Shipped policy. Read-only work runs unattended; anything that mutates the
#: filesystem or spawns a shell is confirmed; well-known secret stores and
#: destructive commands are refused outright.
DEFAULT_POLICY: dict[str, Any] = {
    "*": "allow",
    "write": "ask",
    "edit": "ask",
    "terminal": {
        "*": "ask",
        "ls*": "allow",
        "cat *": "allow",
        "pwd": "allow",
        "git status": "allow",
        "git diff*": "allow",
        "git log*": "allow",
        "rm -rf /*": {"action": "deny", "reason": "Refusing to delete outside the project."},
        "rm -rf ~*": {"action": "deny", "reason": "Refusing to delete the home directory."},
        ":(){*": {"action": "deny", "reason": "Fork bomb."},
        "mkfs*": {"action": "deny", "reason": "Refusing to format a filesystem."},
        "dd if=*of=/dev/*": {"action": "deny", "reason": "Refusing a raw device write."},
    },
    "path": {
        "*": "allow",
        "**/.env": {"action": "deny", "reason": "Environment files hold secrets."},
        "**/.env.*": {"action": "deny", "reason": "Environment files hold secrets."},
        "**/.env.example": "allow",
        "**/.env.sample": "allow",
        "~/.ssh/*": {"action": "deny", "reason": "SSH keys are off-limits."},
        "~/.aws/*": {"action": "deny", "reason": "Cloud credentials are off-limits."},
        "~/.tau/auth.json": {"action": "deny", "reason": "Tau credential store."},
        "**/*.pem": {"action": "deny", "reason": "Private key material."},
        "**/id_rsa*": {"action": "deny", "reason": "Private key material."},
    },
    "external_directory": "ask",
}


@dataclass
class Settings:
    """Non-policy knobs."""

    #: Decision used when there is no way to ask (print/JSON mode).
    headless_default: PermissionState = "deny"
    #: Seconds before an unanswered prompt gives up. Expiry never grants.
    prompt_timeout_seconds: int = 600
    #: Append every decision to ``.tau/extensions/permissions/decisions.log``.
    #:
    #: Off by default: the session now records the same fields on each tool
    #: result — state, surface, pattern, origin, and whether the call then ran
    #: — for allows and denies alike, so leaving this on wrote every decision
    #: twice. Turn it on for a project-scoped file that survives a session
    #: being cleared or deleted, which is the one thing the session cannot do.
    log_decisions: bool = False
    #: Remove denied tools from the schema before the turn starts.
    hide_denied_tools: bool = True


@dataclass
class Policy:
    """A loaded, merged policy ready for the resolver."""

    tool_rules: dict[str, list[Rule]] = field(default_factory=dict)
    tool_states: dict[str, PermissionState] = field(default_factory=dict)
    tool_reasons: dict[str, str] = field(default_factory=dict)
    path_rules: list[Rule] = field(default_factory=list)
    external_rules: list[Rule] = field(default_factory=list)
    default_state: PermissionState = "ask"
    settings: Settings = field(default_factory=Settings)
    #: Scopes that existed but failed to load, for `/permissions` to report.
    invalid_scopes: list[str] = field(default_factory=list)

    def known_tools(self) -> set[str]:
        return set(self.tool_rules) | set(self.tool_states)


def _parse_value(
    raw: Any, origin: RuleOrigin
) -> tuple[PermissionState | None, str | None, list[Rule]]:
    """Normalise one config value.

    A value is either a bare state (``"ask"``), a deny-with-reason object, or a
    map of patterns to either of those. Returns
    ``(flat_state, flat_reason, pattern_rules)`` with exactly one side filled.
    """
    if is_permission_state(raw):
        return raw, None, []

    if isinstance(raw, dict) and raw.get("action") == "deny":
        reason = raw.get("reason")
        return "deny", reason if isinstance(reason, str) else None, []

    if isinstance(raw, dict):
        rules: list[Rule] = []
        for pattern, value in raw.items():
            if is_permission_state(value):
                rules.append(Rule(pattern=pattern, state=value, origin=origin))
            elif isinstance(value, dict) and value.get("action") == "deny":
                reason = value.get("reason")
                rules.append(
                    Rule(
                        pattern=pattern,
                        state="deny",
                        origin=origin,
                        reason=reason if isinstance(reason, str) else None,
                    )
                )
            else:
                raise ValueError(f"invalid permission value for pattern {pattern!r}: {value!r}")
        return None, None, rules

    raise ValueError(f"invalid permission value: {raw!r}")


def _clamp(state: PermissionState) -> PermissionState:
    """Fail-closed clamp applied to a scope that failed to validate."""
    return "ask" if state == "allow" else state


def _read_scope(path: Path) -> tuple[dict[str, Any], bool]:
    """Return ``(config, invalid)``. A missing file is not invalid."""
    if not path.is_file():
        return {}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("permissions: cannot read %s: %s", path, exc)
        return {}, True
    if not isinstance(data, dict):
        _log.warning("permissions: %s is not an object", path)
        return {}, True
    return data, False


def _apply(policy: Policy, config: dict[str, Any], origin: RuleOrigin, clamp: bool) -> None:
    """Merge one scope's ``permission`` map into ``policy``."""
    permission = config.get("permission")
    if not isinstance(permission, dict):
        return

    for key, raw in permission.items():
        try:
            state, reason, rules = _parse_value(raw, origin)
        except ValueError as exc:
            _log.warning("permissions: %s scope, key %r: %s", origin, key, exc)
            continue

        if clamp:
            if state is not None:
                state = _clamp(state)
            rules = [Rule(r.pattern, _clamp(r.state), r.origin, r.reason) for r in rules]

        if key == "path":
            policy.path_rules.extend(rules)
            if state is not None:
                policy.path_rules.append(Rule("*", state, origin, reason))
        elif key == "external_directory":
            policy.external_rules.extend(rules)
            if state is not None:
                policy.external_rules.append(Rule("*", state, origin, reason))
        elif key == "*":
            if state is not None:
                policy.default_state = state
        else:
            if state is not None:
                policy.tool_states[key] = state
                if reason:
                    policy.tool_reasons[key] = reason
                policy.tool_rules.pop(key, None)
            if rules:
                policy.tool_rules.setdefault(key, []).extend(rules)


def _apply_settings(settings: Settings, config: dict[str, Any]) -> None:
    value = config.get("headlessDefault")
    if is_permission_state(value):
        settings.headless_default = value  # type: ignore[assignment]
    value = config.get("promptTimeoutSeconds")
    if isinstance(value, int) and value >= 0:
        settings.prompt_timeout_seconds = value
    value = config.get("logDecisions")
    if isinstance(value, bool):
        settings.log_decisions = value
    value = config.get("hideDeniedTools")
    if isinstance(value, bool):
        settings.hide_denied_tools = value


def load_policy(cwd: Path, *, trusted: bool, home: Path | None = None) -> Policy:
    """Build the effective policy for ``cwd``.

    ``trusted`` gates the project scope only; the global scope and the built-in
    defaults always apply.
    """
    home = home or Path.home()
    policy = Policy()

    _apply(policy, {"permission": DEFAULT_POLICY}, "builtin", clamp=False)

    global_config, global_invalid = _read_scope(home / CONFIG_RELATIVE)
    if global_invalid:
        policy.invalid_scopes.append("global")
    else:
        _apply_settings(policy.settings, global_config)
        _apply(policy, global_config, "global", clamp=False)

    if trusted:
        project_config, project_invalid = _read_scope(cwd / CONFIG_RELATIVE)
        if project_invalid:
            policy.invalid_scopes.append("project")
            # The scope is unusable, so tighten what is already loaded rather
            # than running on a policy the author clearly did not intend.
            policy.default_state = _clamp(policy.default_state)
            policy.tool_states = {k: _clamp(v) for k, v in policy.tool_states.items()}
        else:
            _apply_settings(policy.settings, project_config)
            _apply(policy, project_config, "project", clamp=False)

    return policy
