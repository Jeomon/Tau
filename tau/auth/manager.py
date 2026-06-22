from __future__ import annotations

import builtins
import json
import logging
import os
from pathlib import Path

from tau.auth.storage import AuthStorage, FileAuthStorage, InMemoryAuthStorage
from tau.auth.types import (
    APICredential,
    AuthCredential,
    AuthStatus,
    AuthType,
    LockResult,
    OAuthCredential,
)
from tau.inference.provider.oauth import OAuthLoginCallbacks
from tau.inference.provider.registry import ProviderRegistry
from tau.settings.paths import get_auth_path
from tau.utils.secrets import resolve_secret

_log = logging.getLogger(__name__)


def _get_env_api_key(provider: str) -> str | None:
    """Get API key for a provider from environment variables."""
    return os.environ.get(f"{provider.upper()}_API_KEY")


def _is_unrecoverable_refresh_error(error: Exception) -> bool:
    """Return True when a failed token refresh means the refresh token itself is
    dead (so the user must log in again), rather than a transient network/5xx blip.

    Defers to the shared inference error classifier for the cases it understands
    (auth → unrecoverable; rate-limit/overloaded/server/timeout → transient). OAuth
    token endpoints, however, report a dead refresh token as ``invalid_grant`` /
    HTTP 400 — which the inference-oriented classifier leaves as UNKNOWN — and the
    provider wrappers raise these as ``RuntimeError("Request failed (<code>): ...")``
    without a status_code attribute, so we match those markers explicitly.
    """
    from tau.inference.utils import ErrorKind, classify_error

    kind = classify_error(error).kind
    if kind in (ErrorKind.AUTH, ErrorKind.AUTH_PERMANENT):
        return True
    if kind in (
        ErrorKind.RATE_LIMIT,
        ErrorKind.OVERLOADED,
        ErrorKind.SERVER_ERROR,
        ErrorKind.TIMEOUT,
    ):
        return False
    text = str(error).lower()
    return any(
        m in text
        for m in ("invalid_grant", "invalid_request", "invalid_token", "(400)", "(401)", "(403)")
    )


class AuthManager:
    """Credential storage with pluggable backends."""

    def __init__(self, registry: ProviderRegistry, storage: AuthStorage):
        self.registry = registry
        self.storage = storage
        self.runtime_overrides: dict[str, str] = {}
        self._load_error: Exception | None = None
        self._errors: list[Exception] = []
        self.data: dict[str, AuthCredential] = self._load()

    @staticmethod
    def create(registry: ProviderRegistry, auth_path: Path | None = None) -> AuthManager:
        """Create AuthManager with file storage."""
        path = auth_path or get_auth_path()
        storage = FileAuthStorage(path)
        return AuthManager(registry, storage)

    @staticmethod
    def from_storage(registry: ProviderRegistry, storage: AuthStorage) -> AuthManager:
        """Create AuthManager with custom storage."""
        return AuthManager(registry, storage)

    @staticmethod
    def in_memory(registry: ProviderRegistry, initial: dict | None = None) -> AuthManager:
        """Create AuthManager with in-memory storage for testing."""
        if initial is None:
            initial = {}
        storage = InMemoryAuthStorage()
        storage.with_lock(lambda _: LockResult(result=initial, next=json.dumps(initial, indent=2)))  # type: ignore[arg-type]
        return AuthManager.from_storage(registry, storage)

    def _record_error(self, error: Exception) -> None:
        """Record an error for later retrieval."""
        self._errors.append(error)

    def _parse_storage_data(self, content: str | None) -> dict[str, AuthCredential]:
        """Parse credential data from storage JSON."""
        if not content:
            return {}
        raw_data = json.loads(content)
        data: dict[str, AuthCredential] = {}
        for k, v in raw_data.items():
            cred_type = v.get("type")
            match cred_type:
                case AuthType.OAuth:
                    raw_extra = v.get("extra") or {}
                    extra = (
                        {str(ek): str(ev) for ek, ev in raw_extra.items()}
                        if isinstance(raw_extra, dict)
                        else {}
                    )
                    data[k] = OAuthCredential(
                        access=v.get("access", ""),
                        refresh=v.get("refresh", ""),
                        expires=v.get("expires", 0),
                        extra=extra,
                    )
                case AuthType.ApiKey:
                    data[k] = APICredential(key=v.get("key", ""))
        return data

    def _load(self) -> dict[str, AuthCredential]:
        """Load credentials from storage."""
        try:
            result = self.storage.with_lock(lambda current: LockResult(result=current))
            self._load_error = None
            return self._parse_storage_data(result.result)
        except Exception as e:
            self._load_error = e
            self._record_error(e)
            return {}

    @staticmethod
    def _serialize_credential(credential: AuthCredential) -> dict:
        """Serialize a credential to storable dict format."""
        if isinstance(credential, OAuthCredential):
            return {
                "type": AuthType.OAuth,
                "access": credential.access,
                "refresh": credential.refresh,
                "expires": credential.expires,
                "extra": dict(credential.extra),
            }
        return {"type": AuthType.ApiKey, "key": credential.key}

    def _persist_provider_change(self, provider: str, credential: AuthCredential | None) -> None:
        """Persist a credential change to storage."""
        if self._load_error:
            return

        def update_fn(current: str | None) -> LockResult:
            """Update storage data with new credential."""
            current_data = self._parse_storage_data(current)
            merged = {k: self._serialize_credential(v) for k, v in current_data.items()}
            if credential:
                merged[provider] = self._serialize_credential(credential)
            else:
                merged.pop(provider, None)
            return LockResult(result=None, next=json.dumps(merged, indent=2))

        try:
            self.storage.with_lock(update_fn)
        except Exception as e:
            self._record_error(e)

    def reload(self) -> None:
        """Reload credentials from storage."""
        self.data = self._load()

    def get(self, provider: str) -> AuthCredential | None:
        """Return the stored credential for a provider, or None if not found."""
        return self.data.get(provider)

    def has(self, provider: str) -> bool:
        """Check if credentials exist for a provider in storage."""
        return provider in self.data

    def list(self) -> list[str]:
        """List all providers with stored credentials."""
        return list(self.data.keys())

    def set(self, provider: str, credential: AuthCredential) -> None:
        """Store a credential for a provider and persist to storage."""
        self.data[provider] = credential
        self._persist_provider_change(provider=provider, credential=credential)

    def remove(self, provider: str) -> None:
        """Remove the stored credential for a provider and persist to storage."""
        self.data.pop(provider, None)
        self._persist_provider_change(provider=provider, credential=None)

    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        """Set a runtime API key override (not persisted)."""
        self.runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        """Remove a runtime API key override."""
        self.runtime_overrides.pop(provider, None)

    def get_auth_status(self, provider: str) -> AuthStatus:
        """Return auth status without exposing credential values."""
        if self.has(provider):
            return AuthStatus(configured=True, source="stored")
        if provider in self.runtime_overrides:
            return AuthStatus(configured=True, source="runtime", label="--api-key")
        env_key = f"{provider.upper()}_API_KEY"
        if os.environ.get(env_key):
            return AuthStatus(configured=True, source="env", label=env_key)
        return AuthStatus(configured=False)

    def drain_errors(self) -> builtins.list[Exception]:
        """Return and clear accumulated errors."""
        drained = list(self._errors)
        self._errors.clear()
        return drained

    async def get_api_key(self, provider: str) -> str | None:
        """Get an API key for a provider, refreshing OAuth tokens if needed."""
        # 1. Runtime override
        if provider in self.runtime_overrides:
            return resolve_secret(self.runtime_overrides[provider])

        credential = self.get(provider)

        match credential:
            case APICredential():
                # The stored key may be a literal, "$ENV_VAR", or "!command";
                # resolved once and cached (see tau.utils.secrets).
                return resolve_secret(credential.key)
            case OAuthCredential():
                oauth_provider = self.registry.text.get_oauth_provider(provider=provider)
                if not oauth_provider:
                    return None

                if oauth_provider.is_expired(credential=credential):
                    refreshed_credential = await self._refresh_oauth_token_with_lock(
                        provider=provider
                    )
                    if refreshed_credential:
                        credential = refreshed_credential
                    else:
                        return None
                return oauth_provider.get_api_key(credential=credential)

        # 2. Environment variable fallback
        return _get_env_api_key(provider)

    async def _refresh_oauth_token_with_lock(self, provider: str) -> OAuthCredential | None:
        """Refresh an expired OAuth token with file locking to prevent race conditions."""
        oauth_provider = self.registry.text.get_oauth_provider(provider=provider)
        if not oauth_provider:
            return None

        async def refresh_fn(current: str | None) -> LockResult:
            """Refresh OAuth token in storage."""
            current_data = self._parse_storage_data(current)
            credential = current_data.get(provider)

            if not isinstance(credential, OAuthCredential):
                return LockResult(result=None)

            # Check if another instance already refreshed
            if not oauth_provider.is_expired(credential=credential):
                return LockResult(result=credential)

            try:
                refreshed_credential = await oauth_provider.refresh_token(credential=credential)
                if credential.extra:
                    merged_extra = dict(credential.extra)
                    merged_extra.update(refreshed_credential.extra)
                    refreshed_credential.extra = merged_extra
                current_data[provider] = refreshed_credential
                self.data = current_data
                serialized = {k: self._serialize_credential(v) for k, v in current_data.items()}
                return LockResult(
                    result=refreshed_credential, next=json.dumps(serialized, indent=2)
                )
            except Exception as e:
                _log.error("oauth token refresh failed for %s: %s", provider, e, exc_info=True)
                self._record_error(e)
                return LockResult(result=None)

        result = await self.storage.with_lock_async(refresh_fn)
        return result.result

    def is_oauth(self, provider: str) -> bool:
        """Return True if the stored credential for a provider is an OAuth credential."""
        return isinstance(self.get(provider), OAuthCredential)

    async def force_refresh(
        self, provider: str, stale_access: str | None = None
    ) -> OAuthCredential | None:
        """Force-refresh an OAuth credential whose access token was rejected (e.g. a
        mid-request 401) even though it is not yet time-expired.

        Returns the new credential on success. Returns None if the provider isn't
        OAuth or the refresh failed; on an *unrecoverable* failure (dead refresh
        token) the stored credential is removed so the caller can prompt re-login,
        whereas a transient failure leaves it untouched.

        ``stale_access`` is the access token that was just rejected: if another
        instance has already refreshed it under the lock, we adopt that result
        instead of rotating the refresh token a second time.
        """
        oauth_provider = self.registry.text.get_oauth_provider(provider=provider)
        if not oauth_provider:
            return None

        async def refresh_fn(current: str | None) -> LockResult:
            """Refresh (unconditionally) the OAuth token in storage under the lock."""
            current_data = self._parse_storage_data(current)
            credential = current_data.get(provider)

            if not isinstance(credential, OAuthCredential):
                return LockResult(result=None)

            # Another instance already refreshed after our token was rejected.
            if stale_access is not None and credential.access != stale_access:
                self.data = current_data
                return LockResult(result=credential)

            try:
                refreshed_credential = await oauth_provider.refresh_token(credential=credential)
                if credential.extra:
                    merged_extra = dict(credential.extra)
                    merged_extra.update(refreshed_credential.extra)
                    refreshed_credential.extra = merged_extra
                current_data[provider] = refreshed_credential
                self.data = current_data
                serialized = {k: self._serialize_credential(v) for k, v in current_data.items()}
                return LockResult(
                    result=refreshed_credential, next=json.dumps(serialized, indent=2)
                )
            except Exception as e:
                self._record_error(e)
                if _is_unrecoverable_refresh_error(e):
                    # Refresh token is dead — drop the credential so the user is
                    # prompted to log in again rather than retrying a broken token.
                    current_data.pop(provider, None)
                    self.data = current_data
                    serialized = {k: self._serialize_credential(v) for k, v in current_data.items()}
                    return LockResult(result=None, next=json.dumps(serialized, indent=2))
                # Transient failure — keep the credential for a later retry.
                self.data = current_data
                return LockResult(result=None)

        result = await self.storage.with_lock_async(refresh_fn)
        return result.result

    async def login(self, provider: str, callbacks: OAuthLoginCallbacks):
        """Perform OAuth login flow for a provider and store the resulting credential."""
        if oauth_provider := self.registry.text.get_oauth_provider(provider):
            credential = await oauth_provider.login(callbacks=callbacks)
            self.data[provider] = credential
            self._persist_provider_change(provider, credential)

    async def logout(self, provider: str):
        """Perform OAuth logout for a provider and remove the stored credential."""
        if (
            (oauth_provider := self.registry.text.get_oauth_provider(provider))
            and (credential := self.get(provider))
            and isinstance(credential, OAuthCredential)
        ):
            await oauth_provider.logout(credential=credential)
        self.remove(provider)
