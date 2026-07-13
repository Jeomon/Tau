from __future__ import annotations

import json

import pytest

from tau.auth.manager import AuthManager
from tau.console.commands.doctor import (
    _check_auth,
    _check_environment,
    _check_extensions,
    _check_logs,
    _check_models,
    _check_sessions,
    _check_settings,
)
from tau.inference.model.registry import ModelRegistry
from tau.inference.model.types import Cost, Model
from tau.inference.provider.oauth.types import AbortSignal, OAuthCredential, OAuthLoginCallbacks
from tau.inference.provider.registry import ProviderRegistry
from tau.inference.provider.types import APIProvider, OAuthProvider
from tau.inference.types import LLMOptions
from tau.settings.manager import SettingsManager
from tau.settings.storage import InMemorySettingsStorage
from tau.settings.types import Settings


class _StubOAuthProvider(OAuthProvider):
    """Minimal OAuthProvider stub whose validate()/refresh_token() outcomes are
    controlled by the test.
    """

    def __init__(
        self,
        id: str,
        valid: bool,
        refresh_result: OAuthCredential | Exception | None = None,
    ) -> None:
        super().__init__(id=id, name=id)
        self._valid = valid
        self._refresh_result = refresh_result

    @property
    def api(self) -> str:
        return "stub"

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        raise NotImplementedError

    async def refresh_token(
        self, credential: OAuthCredential, signal: AbortSignal | None = None
    ) -> OAuthCredential:
        if isinstance(self._refresh_result, Exception):
            raise self._refresh_result
        if self._refresh_result is not None:
            return self._refresh_result
        raise NotImplementedError

    async def logout(self, credential: OAuthCredential) -> None:
        raise NotImplementedError

    async def validate(
        self, credential: OAuthCredential, signal: AbortSignal | None = None
    ) -> bool:
        return self._valid


def _model(id: str, provider: str) -> Model:
    return Model(id=id, name=id, provider=provider, cost=Cost())


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_check_settings_passes_when_no_load_errors() -> None:
    sm = SettingsManager(
        storage=InMemorySettingsStorage(),
        initial_global=Settings(),
        initial_project=Settings(),
    )
    section = _check_settings(sm)
    assert all(r.status == "pass" for r in section.results)
    assert len(section.results) == 2


def test_check_settings_reports_global_load_error() -> None:
    sm = SettingsManager(
        storage=InMemorySettingsStorage(),
        initial_global=Settings(),
        initial_project=Settings(),
        global_load_error=ValueError("bad json"),
    )
    section = _check_settings(sm)
    global_result = next(r for r in section.results if "Global" in r.name)
    assert global_result.status == "fail"
    assert "bad json" in global_result.detail


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_check_models_resolves_configured_model() -> None:
    sm = SettingsManager.in_memory({"model": {"text": {"id": "gpt-4", "provider": "openai"}}})
    provider_registry = ProviderRegistry()
    provider_registry.text.register(
        APIProvider(id="openai", name="OpenAI", api="openai_responses", options=LLMOptions())
    )
    model_registry = ModelRegistry()
    model_registry.register(_model("gpt-4", "openai"))

    section, referenced = _check_models(sm, provider_registry, model_registry)

    assert referenced == {"openai"}
    result = next(r for r in section.results if "text model" in r.name)
    assert result.status == "pass"


def test_check_models_flags_unknown_provider_for_known_model_id() -> None:
    sm = SettingsManager.in_memory(
        {"model": {"text": {"id": "gpt-4", "provider": "typo-provider"}}}
    )
    provider_registry = ProviderRegistry()
    provider_registry.text.register(
        APIProvider(id="openai", name="OpenAI", api="openai_responses", options=LLMOptions())
    )
    model_registry = ModelRegistry()
    model_registry.register(_model("gpt-4", "openai"))

    section, _referenced = _check_models(sm, provider_registry, model_registry)

    result = next(r for r in section.results if "text model" in r.name)
    assert result.status == "fail"
    assert "typo-provider" in result.detail


def test_check_models_warns_on_unknown_model_id() -> None:
    sm = SettingsManager.in_memory(
        {"model": {"text": {"id": "no-such-model", "provider": "openai"}}}
    )
    provider_registry = ProviderRegistry()
    model_registry = ModelRegistry()

    section, _referenced = _check_models(sm, provider_registry, model_registry)

    result = next(r for r in section.results if "text model" in r.name)
    assert result.status == "warn"


def test_check_models_reports_none_configured() -> None:
    sm = SettingsManager.in_memory({})
    section, referenced = _check_models(sm, ProviderRegistry(), ModelRegistry())

    assert referenced == set()
    assert len(section.results) == 1
    assert section.results[0].status == "warn"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_auth_passes_for_configured_api_key() -> None:
    registry = ProviderRegistry()
    registry.text.register(
        APIProvider(id="cerebras", name="Cerebras", api="stub", options=LLMOptions())
    )
    auth_manager = AuthManager.in_memory(
        registry, initial={"cerebras": {"type": "api_key", "key": "sk-test"}}
    )

    section = await _check_auth(registry, auth_manager, referenced_providers=set())

    result = next(r for r in section.results if r.name.startswith("cerebras"))
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_check_auth_warns_on_invalid_oauth_token() -> None:
    registry = ProviderRegistry()
    registry.text.register(_StubOAuthProvider(id="stub-oauth", valid=False))
    auth_manager = AuthManager.in_memory(
        registry,
        initial={
            "stub-oauth": {
                "type": "oauth",
                "access": "expired-access",
                "refresh": "refresh",
                "expires": 0,
                "extra": {},
            }
        },
    )

    section = await _check_auth(registry, auth_manager, referenced_providers=set())

    result = next(r for r in section.results if r.name.startswith("stub-oauth"))
    assert result.status == "warn"
    assert "tau auth login stub-oauth" in result.detail


@pytest.mark.asyncio
async def test_check_auth_passes_on_valid_oauth_token() -> None:
    registry = ProviderRegistry()
    registry.text.register(_StubOAuthProvider(id="stub-oauth", valid=True))
    auth_manager = AuthManager.in_memory(
        registry,
        initial={
            "stub-oauth": {
                "type": "oauth",
                "access": "fresh-access",
                "refresh": "refresh",
                "expires": 0,
                "extra": {},
            }
        },
    )

    section = await _check_auth(registry, auth_manager, referenced_providers=set())

    result = next(r for r in section.results if r.name.startswith("stub-oauth"))
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_check_auth_only_reports_referenced_or_stored_providers() -> None:
    registry = ProviderRegistry()
    registry.text.register(
        APIProvider(id="unused", name="Unused", api="stub", options=LLMOptions())
    )
    auth_manager = AuthManager.in_memory(registry, initial={})

    section = await _check_auth(registry, auth_manager, referenced_providers=set())

    assert not any(r.name.startswith("unused") for r in section.results)


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


def test_check_extensions_pass_when_disabled(tmp_path) -> None:
    sm = SettingsManager.in_memory({"extensions": {"enabled": False}})
    section = _check_extensions(sm, tmp_path)
    assert len(section.results) == 1
    assert section.results[0].status == "pass"


def test_check_extensions_flags_missing_entry_path(tmp_path) -> None:
    sm = SettingsManager.in_memory(
        {"extensions": {"list": [{"path": str(tmp_path / "nope"), "name": "missing-ext"}]}}
    )

    section = _check_extensions(sm, tmp_path)

    result = next(r for r in section.results if r.name == "missing-ext")
    assert result.status == "fail"


def test_check_extensions_flags_invalid_manifest_json(tmp_path, monkeypatch) -> None:
    ext_dir = tmp_path / "extensions" / "broken"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text("{not json")
    monkeypatch.setattr(
        "tau.settings.paths.get_extensions_dir", lambda cwd=None: tmp_path / "extensions"
    )

    sm = SettingsManager.in_memory({})
    section = _check_extensions(sm, tmp_path)

    assert any(r.status == "fail" and "manifest" in r.name for r in section.results)


def test_check_extensions_warns_on_missing_tau_key(tmp_path, monkeypatch) -> None:
    ext_dir = tmp_path / "extensions" / "plain"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(json.dumps({"other": {}}))
    monkeypatch.setattr(
        "tau.settings.paths.get_extensions_dir", lambda cwd=None: tmp_path / "extensions"
    )

    sm = SettingsManager.in_memory({})
    section = _check_extensions(sm, tmp_path)

    assert any(r.status == "warn" and "manifest" in r.name for r in section.results)


def test_check_extensions_passes_with_well_formed_manifest(tmp_path, monkeypatch) -> None:
    ext_dir = tmp_path / "extensions" / "good"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(json.dumps({"tau": {"extensions": ["./main.py"]}}))
    monkeypatch.setattr(
        "tau.settings.paths.get_extensions_dir", lambda cwd=None: tmp_path / "extensions"
    )

    sm = SettingsManager.in_memory({})
    section = _check_extensions(sm, tmp_path)

    assert len(section.results) == 1
    assert section.results[0].status == "pass"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_check_sessions_pass_when_no_sessions_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tau.settings.paths.get_sessions_dir", lambda: tmp_path / "sessions")
    section = _check_sessions()
    assert section.results[0].status == "pass"


def test_check_sessions_pass_for_readable_session(tmp_path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    header = {
        "type": "session",
        "version": 1,
        "id": "abc",
        "timestamp": 1700000000.0,
        "cwd": str(tmp_path),
    }
    (sessions_dir / "good.jsonl").write_text(json.dumps(header) + "\n")
    monkeypatch.setattr("tau.settings.paths.get_sessions_dir", lambda: sessions_dir)

    section = _check_sessions()

    assert section.results[0].status == "pass"
    assert "1 session file" in section.results[0].detail


def test_check_sessions_warns_on_corrupt_file(tmp_path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "corrupt.jsonl").write_text("not json at all\n")
    monkeypatch.setattr("tau.settings.paths.get_sessions_dir", lambda: sessions_dir)

    section = _check_sessions()

    result = next(r for r in section.results if "corrupt.jsonl" in r.name)
    assert result.status == "warn"


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


def test_check_logs_pass_when_no_logs_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tau.settings.paths.get_logs_dir", lambda cwd=None: tmp_path / "logs")
    section = _check_logs()
    assert section.results[0].status == "pass"


def test_check_logs_flags_traceback(tmp_path, monkeypatch) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "session1.log").write_text(
        "some log line\nTraceback (most recent call last):\n  File x\nValueError: boom\n"
    )
    monkeypatch.setattr("tau.settings.paths.get_logs_dir", lambda cwd=None: logs_dir)

    section = _check_logs()

    result = next(r for r in section.results if "session1.log" in r.name)
    assert result.status == "warn"


def test_check_logs_pass_when_no_tracebacks(tmp_path, monkeypatch) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "session1.log").write_text("clean startup\nno issues here\n")
    monkeypatch.setattr("tau.settings.paths.get_logs_dir", lambda cwd=None: logs_dir)

    section = _check_logs()

    assert all(r.status == "pass" for r in section.results)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def test_check_environment_reports_python_and_git(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "tau.settings.paths.get_packages_venv", lambda cwd=None: tmp_path / "novenv"
    )
    monkeypatch.setattr("tau.agent.prompt.builder._find_git_root", lambda cwd: None)

    section = _check_environment(tmp_path)

    py_result = next(r for r in section.results if r.name == "Python version")
    assert py_result.status == "pass"
    git_result = next(r for r in section.results if r.name == "Git repository")
    assert git_result.status == "pass"
    assert "not inside" in git_result.detail


def test_check_environment_warns_on_venv_python_mismatch(tmp_path, monkeypatch) -> None:
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    monkeypatch.setattr("tau.settings.paths.get_packages_venv", lambda cwd=None: venv_dir)
    monkeypatch.setattr("tau.extensions.loader._venv_matches_current", lambda path: False)
    monkeypatch.setattr("tau.agent.prompt.builder._find_git_root", lambda cwd: None)

    section = _check_environment(tmp_path)

    venv_result = next(r for r in section.results if "venv" in r.name)
    assert venv_result.status == "warn"


# ---------------------------------------------------------------------------
# --fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_auth_fix_refreshes_expired_token() -> None:
    registry = ProviderRegistry()
    refreshed_credential = OAuthCredential(access="new-access", refresh="new-refresh", expires=0)
    registry.text.register(
        _StubOAuthProvider(id="stub-oauth", valid=False, refresh_result=refreshed_credential)
    )
    auth_manager = AuthManager.in_memory(
        registry,
        initial={
            "stub-oauth": {
                "type": "oauth",
                "access": "expired-access",
                "refresh": "refresh",
                "expires": 0,
                "extra": {},
            }
        },
    )

    section = await _check_auth(registry, auth_manager, referenced_providers=set(), fix=True)

    result = next(r for r in section.results if r.name.startswith("stub-oauth"))
    assert result.status == "pass"
    assert result.fixed is True
    assert "fixed: refreshed" in result.detail


@pytest.mark.asyncio
async def test_check_auth_fix_reports_failure_when_refresh_fails() -> None:
    registry = ProviderRegistry()
    registry.text.register(
        _StubOAuthProvider(
            id="stub-oauth", valid=False, refresh_result=RuntimeError("invalid_grant")
        )
    )
    auth_manager = AuthManager.in_memory(
        registry,
        initial={
            "stub-oauth": {
                "type": "oauth",
                "access": "expired-access",
                "refresh": "refresh",
                "expires": 0,
                "extra": {},
            }
        },
    )

    section = await _check_auth(registry, auth_manager, referenced_providers=set(), fix=True)

    result = next(r for r in section.results if r.name.startswith("stub-oauth"))
    assert result.status == "warn"
    assert result.fixed is False
    assert "tau auth login stub-oauth" in result.detail


@pytest.mark.asyncio
async def test_check_extensions_fix_removes_dangling_global_entry(tmp_path) -> None:
    sm = SettingsManager.in_memory(
        {"extensions": {"list": [{"path": str(tmp_path / "nope"), "name": "missing-ext"}]}}
    )

    section = _check_extensions(sm, tmp_path, fix=True)

    result = next(r for r in section.results if r.name == "missing-ext")
    assert result.status == "pass"
    assert result.fixed is True
    await sm.flush()
    extensions = sm.get_global_settings().extensions
    assert extensions is not None
    assert extensions.list == []


@pytest.mark.asyncio
async def test_check_sessions_fix_quarantines_corrupt_file(tmp_path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    corrupt_file = sessions_dir / "corrupt.jsonl"
    corrupt_file.write_text("not json at all\n")
    monkeypatch.setattr("tau.settings.paths.get_sessions_dir", lambda: sessions_dir)

    section = _check_sessions(fix=True)

    result = next(r for r in section.results if "corrupt.jsonl" in r.name)
    assert result.status == "pass"
    assert result.fixed is True
    assert not corrupt_file.exists()
    assert (sessions_dir / ".corrupt" / "corrupt.jsonl").exists()


def test_check_sessions_fix_not_scanning_quarantine_dir(tmp_path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    (sessions_dir / ".corrupt").mkdir(parents=True)
    (sessions_dir / ".corrupt" / "already-quarantined.jsonl").write_text("garbage\n")
    monkeypatch.setattr("tau.settings.paths.get_sessions_dir", lambda: sessions_dir)

    section = _check_sessions()

    assert section.results[0].status == "pass"
    assert "no sessions yet" in section.results[0].detail
