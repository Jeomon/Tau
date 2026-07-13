from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import fields, replace

from tau.auth.manager import AuthManager
from tau.auth.types import OAuthCredential
from tau.inference.api.registry import LazyAPI
from tau.inference.api.text.registry import LLMAPIRegistry
from tau.inference.model.registry import ModelRegistry
from tau.inference.provider.registry import ProviderRegistry, TextProviderRegistry
from tau.inference.provider.types import OAuthProvider
from tau.inference.types import LLMContext, LLMEvent, LLMOptions, ThinkingLevel
from tau.message.types import LLMMessage, SystemMessage

_log = logging.getLogger(__name__)


class TextLLM:
    """Wrapper around inference APIs with model/provider resolution and option merging."""

    _apis: LLMAPIRegistry | None = None
    _models: ModelRegistry | None = None
    _providers: TextProviderRegistry | None = None
    _auth_manager: AuthManager | None = None

    @classmethod
    def _builtin_apis(cls) -> LLMAPIRegistry:
        if cls._apis is None:
            cls._apis = LLMAPIRegistry.from_builtins()
        return cls._apis

    @classmethod
    def _builtin_models(cls) -> ModelRegistry:
        if cls._models is None:
            cls._models = ModelRegistry.from_text_builtins()
        return cls._models

    @classmethod
    def _builtin_providers(cls) -> TextProviderRegistry:
        if cls._providers is None:
            cls._providers = TextProviderRegistry.from_builtins()
        return cls._providers

    @classmethod
    def _builtin_auth_manager(cls) -> AuthManager:
        if cls._auth_manager is None:
            cls._auth_manager = AuthManager.create(ProviderRegistry(text=cls._builtin_providers()))
        return cls._auth_manager

    def __init__(
        self,
        model_id: str,
        provider: str | None = None,
        options: LLMOptions | None = None,
        *,
        models: ModelRegistry | None = None,
        providers: TextProviderRegistry | None = None,
        apis: LLMAPIRegistry | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        """Initialize an LLM by resolving model, provider, and API implementation.

        Args:
            model_id: The model identifier (e.g., 'claude-3-5-sonnet-latest').
            provider: Optional provider name; if omitted, defaults from model definition.
            options: Optional LLMOptions for API key, base_url, temperature, etc.
            models: Optional custom ModelRegistry; defaults to global builtin registry.
            providers: Optional custom TextProviderRegistry; defaults to global builtin registry.
            apis: Optional custom LLMAPIRegistry; defaults to global builtin registry.
            auth_manager: Optional custom AuthManager; defaults to global builtin store.

        Raises:
            ValueError: If model_id or provider not found in registries.
            RuntimeError: If OAuth provider requires credentials that are unavailable.
        """
        self.requested_model_id = model_id
        self.requested_provider_id = provider
        self.fallback_reason: str | None = None

        _models = models if models is not None else type(self)._builtin_models()
        _providers = providers if providers is not None else type(self)._builtin_providers()
        _apis = apis if apis is not None else type(self)._builtin_apis()
        self._auth_manager = (
            auth_manager if auth_manager is not None else type(self)._builtin_auth_manager()
        )

        # Narrow types for the type checker
        assert _models is not None
        assert _providers is not None
        assert _apis is not None
        assert self._auth_manager is not None

        # When provider is not pinned, try all registered variants of the model
        # in order, skipping OAuth providers whose credentials are missing.
        # This lets `model_id="claude-sonnet-4-6"` work with an API key even if
        # an OAuth variant of the same model is registered first.
        candidates = (
            [_models.get(model_id, provider=provider)]
            if provider is not None
            else _models._models.get(model_id, [])
        )

        # A pinned provider that has no variant of this exact model id is not
        # necessarily an error: the provider may serve models we haven't
        # registered (e.g. a custom OpenAI-compatible deployment, or a new
        # model the provider added). If that provider already has at least one
        # other registered model, synthesize a fallback by copying that
        # model's defaults (api, base_url, cost, capabilities) under the
        # requested id/name rather than refusing to run at all.
        used_custom_model_id = False
        if (not candidates or candidates[0] is None) and provider is not None:
            provider_models = [m for m in _models.list() if m.provider == provider]
            if provider_models:
                # A trailing ":<level>" on a custom id (e.g. "my-model:high") sets
                # that model's default thinking level, mirroring the shorthand
                # some providers use in their own model ids (OpenRouter's ":exacto"
                # etc.). Only strip it when the caller hasn't already pinned a
                # level explicitly — an explicit --thinking wins, and the suffix
                # is then just part of the (unregistered) model id as-is.
                fallback_id = model_id
                thinking_level = None
                if options is None or options.thinking_level is None:
                    prefix, _, suffix = model_id.rpartition(":")
                    if prefix and suffix in set(ThinkingLevel):
                        fallback_id = prefix
                        thinking_level = ThinkingLevel(suffix)
                base = replace(provider_models[0], id=fallback_id, name=fallback_id)
                if thinking_level is not None:
                    base = replace(base, thinking=True, thinking_level=thinking_level)
                candidates = [base]
                used_custom_model_id = True

        if not candidates or candidates[0] is None:
            raise ValueError(f"Model '{model_id}' not found.")

        model = None
        resolved_provider = None
        skipped_providers: list[str] = []
        for candidate in candidates:
            cand_provider = _providers.get(candidate.provider)  # type: ignore[union-attr]
            if cand_provider is None:
                skipped_providers.append(
                    f"provider '{candidate.provider}' is not registered"  # type: ignore[union-attr]
                )
                continue
            if isinstance(cand_provider, OAuthProvider) and not isinstance(
                self._auth_manager.get(cand_provider.id), OAuthCredential
            ):
                skipped_providers.append(f"provider '{cand_provider.id}' has no OAuth credentials")
                continue  # no credentials — try next variant
            model = candidate
            resolved_provider = cand_provider
            break

        if model is None or resolved_provider is None:
            tried = [c.provider for c in candidates]  # type: ignore[union-attr]
            raise RuntimeError(
                f"No usable provider found for '{model_id}'. "
                f"Tried: {tried}. Log in or set an API key."
            )

        self.model = model
        if used_custom_model_id:
            self.fallback_reason = (
                f"Model '{model_id}' not found for provider '{resolved_provider.id}'. "
                "Using custom model id."
            )
        elif skipped_providers:
            self.fallback_reason = (
                "; ".join(skipped_providers)
                + f"; selected provider '{resolved_provider.id}' for the same model"
            )

        # Keep the API reference unresolved (a "module:Class" string stays a
        # string) so the provider SDK is not imported until the first request.
        api_ref = model.api or resolved_provider.api

        base_url_override = model.base_url

        if isinstance(resolved_provider, OAuthProvider):
            credential = self._auth_manager.get(resolved_provider.id)
            if not isinstance(credential, OAuthCredential):
                raise RuntimeError(
                    f"No credentials found for '{resolved_provider.id}'. Please log in first."
                )
            base_opts = LLMOptions(api_key=resolved_provider.get_api_key(credential))
            if base_url_override:
                base_opts.base_url = base_url_override
            merged = self._merge_options(base_opts, options)
            self.provider_id = resolved_provider.id
        else:
            base_opts = resolved_provider.options
            if base_url_override:
                override_opts = LLMOptions(base_url=base_url_override)
                base_opts = self._merge_options(base_opts, override_opts)
            merged = self._merge_options(base_opts, options)
            self.provider_id = resolved_provider.id

        if merged.max_tokens is None:
            merged.max_tokens = model.max_output_tokens

        # Resolve any "$ENV_VAR" / "!command" references in custom headers to
        # their values. Done once here (per provider/model selection); the
        # resolver memoizes, so a !command runs only the first time it's seen.
        if merged.headers:
            from tau.utils.secrets import resolve_secrets

            merged.headers = resolve_secrets(merged.headers)

        # Lazy adapter: exposes `.options` immediately but only imports the
        # provider SDK and builds its client on first `.stream()`/`.invoke()`.
        self.api = LazyAPI(_apis, api_ref, merged)

    @classmethod
    def list_available(cls) -> list:
        """Return all text models whose provider has usable auth (credential or env var)."""
        from tau.inference.api.availability import available_models

        return available_models(
            cls._builtin_models(), cls._builtin_providers(), cls._builtin_auth_manager()
        )

    def _merge_options(self, base: LLMOptions, override: LLMOptions | None) -> LLMOptions:
        """Merge base options with override options, preferring non-None override values.

        Args:
            base: The base LLMOptions configuration.
            override: Optional override LLMOptions; fields override base when non-None.

        Returns:
            A new LLMOptions with merged values.
        """
        if override is None:
            return base
        merged = LLMOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    def _resolve_messages(self, context: LLMContext) -> list[LLMMessage]:
        """Resolve messages for the LLM call, prepending system prompt if needed.

        Args:
            context: The LLMContext with messages and optional system prompt.

        Returns:
            A list of LLMMessages with system message injected if needed.
        """
        messages = context.messages
        if context.system_prompt and (not messages or not isinstance(messages[0], SystemMessage)):
            messages = [SystemMessage.text(context.system_prompt)] + messages
        return messages

    async def stream(self, context: LLMContext) -> AsyncGenerator[LLMEvent, None]:
        """Stream LLM events from the configured provider API.

        Retries transient errors transparently as long as no events have been
        yielded yet (pre-stream failures). Once streaming has started, errors
        are forwarded as ErrorEvent because already-yielded events can't be
        recalled.

        Args:
            context: The LLMContext with messages, tools, and response format options.

        Yields:
            LLMEvent objects (TextDeltaEvent, ToolCallEndEvent, EndEvent, ErrorEvent, etc.).
        """
        import asyncio

        from tau.inference.types import (
            ErrorEvent,
            RetryEvent,
            StartEvent,
            StopReason,
            TextDeltaEvent,
            TextEndEvent,
            ToolCallEndEvent,
        )
        from tau.inference.utils import ErrorKind, classify_error, get_retry_after_delay

        api_key = await self._auth_manager.get_api_key(self.provider_id)  # type: ignore[union-attr]
        if api_key:
            # Set credentials before resolving the lazy adapter: SDK clients such as
            # Anthropic validate and capture authentication during construction.
            self.api.options.api_key = api_key

        # Resolve the provider SDK (first-use import + client construction) on a
        # worker thread so a cold import doesn't block the event loop — and with
        # it, the render loop's spinner animation.
        await self.api.resolve_async()

        messages = self._resolve_messages(context)
        api_context = LLMContext(
            messages=messages,
            tools=context.tools,
            response_format=context.response_format,
            ephemeral_message_count=context.ephemeral_message_count,
        )

        max_retries = self.api.options.max_retries
        base_delay_s = self.api.options.retry_base_delay_ms / 1000

        attempt = 0
        oauth_recovery_attempted = False
        model_name = getattr(self.model, "name", getattr(self.model, "id", "unknown"))
        _log.debug("stream: provider=%s model=%s", self.provider_id, model_name)
        while True:
            received_any = False
            received_content = False
            try:
                async with aclosing(self.api.stream(api_context, model=self.model)) as stream:
                    async for event in stream:
                        # StartEvent/RetryEvent are emitted locally before any HTTP round-trip;
                        # don't count them as "received data" so retries still fire on empty bodies.
                        if not isinstance(event, (StartEvent, RetryEvent)):
                            received_any = True
                        if isinstance(event, (TextEndEvent, TextDeltaEvent, ToolCallEndEvent)):
                            received_content = True
                        yield event
                if not received_content and attempt < max_retries:
                    _log.warning(
                        "empty response from %s/%s, retrying (attempt %d/%d)",
                        self.provider_id,
                        getattr(self.model, "name", getattr(self.model, "id", "unknown")),
                        attempt + 1,
                        max_retries,
                    )
                    yield RetryEvent(
                        attempt=attempt + 1, max_retries=max_retries, error="empty response"
                    )
                    await asyncio.sleep(base_delay_s * (2**attempt))
                    attempt += 1
                    continue
                return
            except Exception as e:
                classified = classify_error(e)

                # OAuth token rejected before any data arrived: the access token was
                # invalidated server-side (revoked/rotated) though not time-expired,
                # so get_api_key's expiry check didn't catch it. Force a refresh and
                # retry once for free; if the refresh token is dead, prompt re-login.
                if (
                    classified.kind == ErrorKind.AUTH
                    and not received_any
                    and not oauth_recovery_attempted
                    and self._auth_manager.is_oauth(self.provider_id)  # type: ignore[union-attr]
                ):
                    _log.debug("oauth token refresh: provider=%s", self.provider_id)
                    oauth_recovery_attempted = True
                    refreshed = await self._auth_manager.force_refresh(  # type: ignore[union-attr]
                        self.provider_id, stale_access=self.api.options.api_key
                    )
                    if refreshed is not None:
                        self.api.options.api_key = refreshed.access
                        yield RetryEvent(attempt=attempt + 1, max_retries=max_retries, error=str(e))
                        continue  # free retry — does not consume a normal attempt
                    if not self._auth_manager.has(self.provider_id):  # type: ignore[union-attr]
                        yield ErrorEvent(
                            reason=StopReason.Error,
                            error=(
                                "Authentication failed — your session has expired."
                                " Run /login to sign in again."
                            ),
                        )
                        return
                    # Transient refresh failure: fall through to standard handling.

                if received_any or not classified.retryable or attempt >= max_retries:
                    _log.error(
                        "inference error from %s/%s: %s",
                        self.provider_id,
                        getattr(self.model, "name", getattr(self.model, "id", "unknown")),
                        e,
                    )
                    yield ErrorEvent(reason=StopReason.Error, error=str(e), kind=classified.kind)
                    return
                _log.warning(
                    "transient error from %s/%s: %s, retrying (attempt %d/%d)",
                    self.provider_id,
                    getattr(self.model, "name", getattr(self.model, "id", "unknown")),
                    e,
                    attempt + 1,
                    max_retries,
                )
                yield RetryEvent(attempt=attempt + 1, max_retries=max_retries, error=str(e))
                await asyncio.sleep(get_retry_after_delay(e, base_delay_s * (2**attempt)))
                attempt += 1

    async def invoke(
        self,
        context: LLMContext,
        thinking_level: ThinkingLevel | None = None,
    ) -> list[LLMEvent]:
        import asyncio

        from tau.inference.types import ErrorEvent, StopReason, ThinkingLevel
        from tau.inference.utils import ErrorKind, classify_error, get_retry_after_delay

        api_key = await self._auth_manager.get_api_key(self.provider_id)  # type: ignore[union-attr]
        if api_key:
            # Set credentials before resolving the lazy adapter; see stream().
            self.api.options.api_key = api_key

        # Resolve the provider SDK (first-use import + client construction) on a
        # worker thread so a cold import doesn't block the event loop — and with
        # it, the render loop's spinner animation.
        await self.api.resolve_async()

        original = self.api.options.thinking_level
        if thinking_level is not None and thinking_level != ThinkingLevel.Off:
            self.api.options.thinking_level = thinking_level

        max_retries = self.api.options.max_retries
        base_delay_s = self.api.options.retry_base_delay_ms / 1000

        messages = self._resolve_messages(context)
        api_context = LLMContext(
            messages=messages,
            tools=context.tools,
            response_format=context.response_format,
            ephemeral_message_count=context.ephemeral_message_count,
        )

        attempt = 0
        oauth_recovery_attempted = False
        model_name = getattr(self.model, "name", getattr(self.model, "id", "unknown"))
        _log.debug("invoke: provider=%s model=%s", self.provider_id, model_name)
        try:
            while True:
                try:
                    from tau.inference.types import TextDeltaEvent, TextEndEvent, ToolCallEndEvent

                    events = await self.api.invoke(api_context, model=self.model)

                    has_content = any(
                        (isinstance(e, ToolCallEndEvent))
                        or (
                            isinstance(e, (TextEndEvent, TextDeltaEvent)) and e.text.content.strip()
                        )
                        for e in events
                    )

                    if not has_content and attempt < max_retries:
                        _log.warning(
                            "empty response from %s/%s, retrying (attempt %d/%d)",
                            self.provider_id,
                            getattr(self.model, "name", getattr(self.model, "id", "unknown")),
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(base_delay_s * (2**attempt))
                        attempt += 1
                        continue

                    return events
                except Exception as e:
                    classified = classify_error(e)

                    # OAuth access token rejected though not time-expired: force a
                    # refresh and retry once; if the refresh token is dead, ask the
                    # user to re-login (mirrors the recovery path in stream()).
                    if (
                        classified.kind == ErrorKind.AUTH
                        and not oauth_recovery_attempted
                        and self._auth_manager.is_oauth(self.provider_id)  # type: ignore[union-attr]
                    ):
                        _log.debug("oauth token refresh: provider=%s", self.provider_id)
                        oauth_recovery_attempted = True
                        refreshed = await self._auth_manager.force_refresh(  # type: ignore[union-attr]
                            self.provider_id, stale_access=self.api.options.api_key
                        )
                        if refreshed is not None:
                            self.api.options.api_key = refreshed.access
                            continue  # free retry, does not consume attempt
                        if not self._auth_manager.has(self.provider_id):  # type: ignore[union-attr]
                            return [
                                ErrorEvent(
                                    reason=StopReason.Error,
                                    error=(
                                        "Authentication failed — your session has expired."
                                        " Run /login to sign in again."
                                    ),
                                )
                            ]

                    if not classified.retryable or attempt >= max_retries:
                        _log.error(
                            "inference error from %s/%s: %s",
                            self.provider_id,
                            getattr(self.model, "name", getattr(self.model, "id", "unknown")),
                            e,
                        )
                        return [
                            ErrorEvent(reason=StopReason.Error, error=str(e), kind=classified.kind)
                        ]

                    _log.warning(
                        "transient error from %s/%s: %s, retrying (attempt %d/%d)",
                        self.provider_id,
                        getattr(self.model, "name", getattr(self.model, "id", "unknown")),
                        e,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(get_retry_after_delay(e, base_delay_s * (2**attempt)))
                    attempt += 1
        finally:
            self.api.options.thinking_level = original
