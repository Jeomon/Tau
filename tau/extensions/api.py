from __future__ import annotations

import contextlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM
    from tau.settings.manager import SettingsManager
    from tau.tool.types import Tool


# ── Shared runtime reference ───────────────────────────────────────────────────


class _RuntimeRef:
    """Mutable holder filled by Runtime.__init__() after context creation.

    Allows handlers registered during extension load to obtain the live runtime
    when they actually fire (which is always after the runtime is bound).

    ``services`` is a shared registry (one dict across every extension) so one
    extension can publish a service object via ``tau.provide(name, obj)`` and
    another can resolve it via ``tau.get_service(name)``.
    """

    __slots__ = ("runtime", "service_owners", "services")

    def __init__(self) -> None:
        self.runtime: Any = None
        self.services: dict[str, Any] = {}
        self.service_owners: dict[str, str] = {}


# ── Per-extension state ────────────────────────────────────────────────────────


@dataclass
class ExtensionError:
    """Records a non-fatal error that occurred while loading or dispatching an extension."""

    extension_path: str
    event: str
    error: str
    stack: str | None = None


@dataclass
class ExtensionSettingsRegistration:
    """Settings items an extension wants to expose in the /settings panel."""

    title: str
    items: list[Any]  # list[SettingItem] — imported lazily to avoid circular deps
    on_change: Callable[[str, str], None]
    summary: str = ""  # value shown on the parent row (e.g. "on"/"off"); "" → "→"
    summary_key: str = ""  # full key whose change should refresh ``summary``


@dataclass
class Extension:
    """All state accumulated for a single loaded extension module."""

    path: str
    config: dict = field(default_factory=dict)
    source: str = "unknown"  # "builtin" | "project" | "global" | "package" | "explicit" | "unknown"
    handlers: dict[str, list[Callable]] = field(default_factory=dict)
    tools: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)
    shortcuts: list[ShortcutRegistration] = field(default_factory=list)
    prompt_appends: list[str] = field(default_factory=list)
    message_renderers: dict[str, Callable] = field(default_factory=dict)
    autocomplete_providers: list[Any] = field(
        default_factory=list
    )  # list[AutocompleteRegistration]
    settings_registrations: list[ExtensionSettingsRegistration] = field(default_factory=list)


@dataclass
class LoadExtensionsResult:
    """Aggregated outcome of loading a batch of extension files."""

    extensions: list[Extension] = field(default_factory=list)
    errors: list[ExtensionError] = field(default_factory=list)


@runtime_checkable
class ExtensionFactory(Protocol):
    """Callable that registers one in-memory extension."""

    def __call__(self, tau: ExtensionAPI) -> Awaitable[None] | None:
        """Register extension capabilities through the standard API."""
        ...


# ── Deferred registration types ───────────────────────────────────────────────


@dataclass
class ShortcutRegistration:
    """A literal keyboard shortcut registered by an extension."""

    key: str
    description: str | None
    handler: Callable[[Any], Awaitable[None] | None]
    extension_path: str


@dataclass
class FlagRegistration:
    """A CLI/env flag declared by an extension."""

    name: str
    type: Literal["bool", "str", "int"]
    default: bool | str | int | None
    description: str | None
    env: str | None


# ── Exec result ───────────────────────────────────────────────────────────────


@dataclass
class ExecResult:
    """Result from tau.exec()."""

    stdout: str
    stderr: str
    code: int | None


# ── Provider helpers ──────────────────────────────────────────────────────────


def _resolve_api_key(value: str) -> str:
    """Resolve an api_key value: literal, $ENV_VAR, or !shell-command."""
    from tau.utils.secrets import resolve_secret

    return resolve_secret(value)


def _parse_modalities(values: list[str]) -> list:
    """Convert a list of modality strings to Modality enum values, ignoring unknowns."""
    from tau.inference.model.types import Modality

    result = []
    for v in values:
        with contextlib.suppress(ValueError):
            result.append(Modality(v.lower()))
    return result


def _build_stream_api(stream_fn: Callable) -> type:
    """Wrap a user ``stream`` callable in a ``BaseLLMAPI`` subclass.

    ``stream_fn`` is an async generator ``(context, model, options)`` yielding
    ``LLMEvent`` objects. The returned class can be passed as a provider's
    ``api`` so the agent drives a fully custom transport.
    """
    from tau.inference.api.text.base import BaseLLMAPI

    class _ConfigStreamAPI(BaseLLMAPI):
        async def stream(self, context, model):  # type: ignore[override]
            async for event in stream_fn(context, model, self.options):
                yield event

    return _ConfigStreamAPI


def _build_oauth_provider(provider_id: str, name: str, api_impl: Any, oauth: dict) -> Any:
    """Build an ``OAuthProvider`` from an extension's ``oauth`` config block.

    Required: ``login`` (async ``(callbacks) -> OAuthCredential``).
    Optional: ``refresh_token``, ``logout``, ``validate``, ``get_api_key``,
    ``name``, ``uses_callback_server``.
    """
    from tau.inference.provider.types import OAuthProvider
    from tau.inference.types import AuthType

    login_fn = oauth.get("login")
    refresh_fn = oauth.get("refresh_token")
    logout_fn = oauth.get("logout")
    validate_fn = oauth.get("validate")
    get_key_fn = oauth.get("get_api_key")
    disp_name = oauth.get("name", name)
    callback_server = bool(oauth.get("uses_callback_server", False))

    class _ConfigOAuthProvider(OAuthProvider):
        def __init__(self) -> None:
            self.id = provider_id
            self.name = disp_name
            self.auth_type = AuthType.OAuth
            self.uses_callback_server = callback_server

        @property
        def api(self):
            return api_impl

        async def login(self, callbacks):
            if login_fn is None:
                raise NotImplementedError("oauth config is missing a 'login' callable")
            return await login_fn(callbacks)

        async def refresh_token(self, credential, signal=None):
            if refresh_fn is None:
                return credential
            return await refresh_fn(credential, signal)

        async def logout(self, credential):
            if logout_fn is not None:
                await logout_fn(credential)

        async def validate(self, credential, signal=None):
            if validate_fn is not None:
                return await validate_fn(credential, signal)
            return not self.is_expired(credential)

        def get_api_key(self, credential):
            if get_key_fn is not None:
                return get_key_fn(credential)
            return credential.access

    return _ConfigOAuthProvider()


# ── ExtensionAPI ──────────────────────────────────────────────────────────────


class ExtensionAPI:
    """
    Registration-time API passed to each extension's ``register(tau)`` function.

    Everything called here is stored in the extension's ``Extension`` object and
    applied to the runtime after all extensions finish loading.  Live session
    state is not available here — use ``ExtensionContext`` (passed to command
    and shortcut handlers) for that.

    Quick reference::

        from dataclasses import dataclass, field
        from tau.extensions import ExtensionSettings

        @dataclass
        class MyConfig:
            api_key: str = ""
            timeout_ms: int = 5000

        def register(tau):
            # All extensions use ExtensionSettings for type-safe config
            config = ExtensionSettings(MyConfig, tau.config)
            api_key = config.get("api_key")
            timeout = config.get("timeout_ms")

            # Events — handler always receives (event, ctx)
            @tau.on("agent_end")
            async def on_end(event, ctx):
                print(ctx.model_id)

            # Tools
            tau.register_tool(my_tool)

            # Commands — handler receives (ctx, args)
            async def greet(ctx, args):
                print(f"hello from {ctx.cwd}")
            tau.register_command("greet", "Say hello", greet)

            # Keyboard shortcuts — handler receives (ctx,)
            @tau.register_shortcut("ctrl+g", "Open greeter")
            async def on_ctrl_g(ctx):
                print("ctrl+g")

            # Prompt
            tau.append_prompt("Always respond concisely.")

            # Themes
            from tau.tui.theme import LayoutTheme
            tau.register_theme("my-theme", LayoutTheme(...))

            # Message renderers (custom message types in the TUI)
            def render_banner(msg, theme, width):
                return [theme.info(msg.contents[0].content)]
            tau.register_message_renderer("banner", render_banner)

            # Flags (env-backed, for values not suited to settings.json)
            tau.register_flag("token", type="str", env="MY_EXT_TOKEN")
            token = tau.get_flag("token")

            # Shell exec (async — use inside event/command handlers)
            @tau.on("session_shutdown")
            async def on_shutdown(event, ctx):
                result = await tau.exec("git", ["status"])
                print(result.stdout)

    Configuration is loaded from the ``settings`` dict of the matching entry
    in ``extensions.list`` (in settings.json) and passed to ``ExtensionSettings``
    for type-safe access with validation and nested structure support. It is an
    empty dict when no entry is found or no ``settings`` key is present::

        # settings.json
        {
          "extensions": {
            "list": [
              {
                "path": "~/.tau/extensions/my_ext.py",
                "settings": { "api_key": "sk-...", "verbose": true }
              }
            ]
          }
        }
    """

    def __init__(
        self,
        extension: Extension,
        llm: TextLLM,
        settings: SettingsManager,
        cwd: Path,
        runtime_ref: _RuntimeRef | None = None,
    ) -> None:
        self._extension = extension
        self._llm = llm
        self._settings = settings
        self._cwd = cwd
        self._runtime_ref = runtime_ref
        self._flags: dict[str, FlagRegistration] = {}

    # ── Events ────────────────────────────────────────────────────────────────

    def on(self, event_type: str, handler: Callable | None = None) -> Any:
        """Subscribe to a lifecycle event by its string type.

        Handlers always receive ``(event, ctx: ExtensionContext)`` — both
        arguments are always injected by the dispatch layer.

        Direct call:
            tau.on("session_start", my_handler)

        Decorator:
            @tau.on("agent_end")
            async def handler(event, ctx): ...
        """
        if handler is not None:
            self._extension.handlers.setdefault(event_type, []).append(handler)
            return None

        def decorator(fn: Callable) -> Callable:
            """Register the function as an event handler."""
            self._extension.handlers.setdefault(event_type, []).append(fn)
            return fn

        return decorator

    # ── Tools ─────────────────────────────────────────────────────────────────

    def register_tool(self, tool: Tool) -> None:
        """Register a tool the agent can call."""
        self._extension.tools[tool.name] = tool

    # ── Inter-extension services ──────────────────────────────────────────────

    def provide(self, name: str, service: Any) -> None:
        """Publish a service object so other extensions can depend on it.

        Call this from ``register()`` (the provider always loads first or the
        consumer simply finds nothing). Consumers should resolve the service from
        a ``runtime_ready`` handler, by which point every extension has loaded.

        Example (provider)::

            def register(tau):
                service = LSP(cwd=tau.cwd)
                tau.provide("lsp", service)
        """
        if self._runtime_ref is None:
            return
        self._runtime_ref.services[name] = service
        self._runtime_ref.service_owners[name] = self._extension.path

    def get_service(self, name: str) -> Any | None:
        """Resolve a service published by another extension via ``provide``.

        Returns ``None`` when no extension has registered that name — treat that
        as an optional/soft dependency rather than an error.

        Example (consumer)::

            @tau.on("runtime_ready")
            async def _setup(event, ctx):
                lsp = tau.get_service("lsp")
                if lsp is None:
                    return   # provider not installed/enabled
                ...
        """
        if self._runtime_ref is None:
            return None
        return self._runtime_ref.services.get(name)

    # ── Commands ──────────────────────────────────────────────────────────────

    def register_command(
        self,
        name: str,
        description: str,
        handler: Callable[[Any, list[str]], Awaitable[None] | None],
        aliases: list[str] | None = None,
        get_argument_completions: Callable[[str], list[Any]] | None = None,
        argument_hint: str | None = None,
        requires_idle: bool = True,
    ) -> None:
        """Register a slash command.

        The handler receives ``(ctx: ExtensionContext, args: list[str])``.

        ``get_argument_completions(prefix)`` is called while the user types
        arguments after ``/name `` and should return a list of
        ``AutocompleteItem`` objects to display in the picker.

        ``argument_hint`` is shown as inline ghost text in the input after the
        command name, e.g. ``"<file> <description>"``.  Each ``<token>``
        disappears as the user fills in that positional argument.

        ``requires_idle=False`` allows UI-only or read-only commands to run
        while an agent turn is active. Such commands must not mutate turn,
        model, tool, session, or extension state.
        """
        from tau.commands.types import CommandInfo

        user_handler = handler
        runtime_ref = self._runtime_ref

        def _call(registry: Any, args: list[str]) -> Awaitable[None] | None:
            from tau.extensions.context import ExtensionContext

            runtime = runtime_ref.runtime if runtime_ref is not None else None
            if runtime is None:
                return None
            ctx = ExtensionContext.from_runtime(runtime)
            return user_handler(ctx, args)

        self._extension.commands[name] = CommandInfo(
            name=name,
            description=description,
            call=_call,
            aliases=aliases or [],
            get_argument_completions=get_argument_completions,
            argument_hint=argument_hint,
            requires_idle=requires_idle,
        )

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def register_shortcut(
        self,
        key: str,
        description: str | None = None,
        handler: Callable[[Any], Awaitable[None] | None] | None = None,
    ) -> Any:
        """Register a global keyboard shortcut.

        The handler receives ``(ctx: ExtensionContext,)``.

        Direct call:
            tau.register_shortcut("ctrl+g", "Open greeter", my_handler)

        Decorator:
            @tau.register_shortcut("ctrl+g", "Open greeter")
            async def handler(ctx): ...
        """
        if handler is not None:
            self._extension.shortcuts.append(
                ShortcutRegistration(key, description, handler, self._extension.path)
            )
            return None

        def decorator(fn: Callable) -> Callable:
            self._extension.shortcuts.append(
                ShortcutRegistration(key, description, fn, self._extension.path)
            )
            return fn

        return decorator

    # ── Message renderers ─────────────────────────────────────────────────────

    def register_message_renderer(self, custom_type: str, renderer: Callable) -> None:
        """Register a renderer for a custom message type.

        ``renderer(message, theme, width) -> list[str]``

        Called by MessageList when it encounters a CustomMessage whose
        ``custom_type`` matches ``custom_type``.
        """
        self._extension.message_renderers[custom_type] = renderer

    # ── Autocomplete providers ────────────────────────────────────────────────

    def add_autocomplete_provider(
        self,
        trigger: str,
        get_items: Callable,
        description: str = "",
    ) -> None:
        """Register an autocomplete provider activated by a trigger character.

        ``trigger`` is a single character (e.g. ``"#"``, ``":"``, ``"!"``) that
        activates the provider when typed in the editor.  ``get_items`` receives
        an ``AutocompleteContext`` and returns a list of ``AutocompleteItem``
        objects — it may be sync or async.

        Example::

            from tau.tui.autocomplete import AutocompleteItem

            async def issues(ctx):
                issues = await fetch_issues(query=ctx.query)
                return [AutocompleteItem(label=f"#{i.id}", description=i.title) for i in issues]

            tau.add_autocomplete_provider("#", issues, description="GitHub issues")
        """
        if len(trigger) != 1:
            raise ValueError(f"trigger must be a single character, got {trigger!r}")
        from tau.tui.autocomplete import AutocompleteRegistration

        reg = AutocompleteRegistration(
            trigger=trigger,
            get_items=get_items,
            description=description,
        )
        self._extension.autocomplete_providers.append(reg)

    # ── Providers ─────────────────────────────────────────────────────────────

    def register_provider(self, provider_id: str, config: dict) -> None:
        """Register a custom LLM provider so it appears alongside built-ins.

        Takes effect immediately — any ``TextLLM`` created after this call will
        be able to use the new provider.

        Config keys:
          ``name``      (str, required)  — display name shown in the model picker.
          ``api``       (str, required)  — API type: ``"openai_completions"``,
                        ``"openai_responses"``, ``"anthropic_messages"``,
                        ``"gemini_generate"``, ``"mistral_chat"``, ``"ollama_chat"``.
          ``base_url``  (str)            — API endpoint URL.
          ``api_key``   (str)            — literal key, ``"$ENV_VAR"`` to read from
                        the environment, or ``"!command"`` to run a shell command
                        and use its stdout as the key.
          ``headers``   (dict[str,str])  — extra HTTP headers sent with each
                        request. Values may also be ``"$ENV_VAR"`` / ``"!command"``
                        references, resolved once at runtime (cached).
          ``auth_header`` (bool)         — when true, adds an
                        ``Authorization: Bearer <api_key>`` header automatically.
          ``stream``    (callable)       — custom transport. An async generator
                        ``(context, model, options)`` yielding ``LLMEvent``
                        objects. Replaces the built-in ``api`` so the agent talks
                        to a fully custom backend.
          ``oauth``     (dict)           — register an OAuth provider for
                        ``/login`` support instead of a static key. Keys:
                        ``login`` (async ``(callbacks) -> OAuthCredential``,
                        required), and optional ``refresh_token``, ``logout``,
                        ``validate``, ``get_api_key``, ``name``,
                        ``uses_callback_server``.
          ``models``    (list[dict])     — model definitions, each with at minimum
                        ``{"id": "model-name"}``.
                        Optional fields: ``name`` (str), ``context_window`` (int),
                        ``max_input_tokens`` (int), ``max_output_tokens`` (int,
                        alias ``max_tokens``), ``input_price`` (float),
                        ``output_price`` (float), ``thinking`` (bool),
                        ``input`` (list[str]), ``output`` (list[str]).
                        Modality strings: ``"text"``, ``"image"``, ``"audio"``, ``"video"``.
                        Defaults to ``["text"]`` for both input and output.

        Example::

            tau.register_provider("my-llm", {
                "name": "My LLM",
                "api": "openai_completions",
                "base_url": "https://api.my-llm.com/v1",
                "api_key": "$MY_LLM_API_KEY",
                "models": [
                    {"id": "my-model-7b", "context_window": 32768},
                ],
            })
        """
        from tau.inference.api.text.service import TextLLM
        from tau.inference.model.types import Cost, Model
        from tau.inference.provider.types import APIProvider
        from tau.inference.types import LLMOptions

        name = config.get("name") or provider_id
        api = config.get("api", "openai_completions")
        base_url: str | None = config.get("base_url")
        raw_key: str | None = config.get("api_key")
        api_key = _resolve_api_key(raw_key) if raw_key else None
        # Custom headers are stored as-is (values may be "$ENV_VAR"/"!command");
        # they are resolved at request-construction time, once and cached.
        headers: dict | None = config.get("headers")

        # Custom transport: a user `stream` callable replaces the built-in api.
        stream_fn = config.get("stream")
        api_impl: Any = _build_stream_api(stream_fn) if stream_fn is not None else api

        oauth_cfg = config.get("oauth")
        if oauth_cfg is not None:
            provider: Any = _build_oauth_provider(provider_id, name, api_impl, oauth_cfg)
        else:
            if config.get("auth_header") and api_key:
                headers = {**(headers or {}), "Authorization": f"Bearer {api_key}"}
            provider = APIProvider(
                id=provider_id,
                name=name,
                api=api_impl,
                options=LLMOptions(base_url=base_url, api_key=api_key, headers=headers),
            )
        TextLLM._builtin_providers().register(provider)

        models = TextLLM._builtin_models()
        for m in config.get("models", []):
            model_id = m.get("id")
            if not model_id:
                continue
            models.register(
                Model(
                    id=model_id,
                    name=m.get("name", model_id),
                    provider=m.get("provider", provider_id),
                    context_window=m.get("context_window", 0),
                    max_input_tokens=m.get("max_input_tokens"),
                    max_output_tokens=m.get("max_output_tokens", m.get("max_tokens", 16384)),
                    cost=Cost(
                        input=m.get("input_price", 0.0),
                        output=m.get("output_price", 0.0),
                    ),
                    input=_parse_modalities(m.get("input", ["text"])),
                    output=_parse_modalities(m.get("output", ["text"])),
                    thinking=m.get("thinking", False),
                )
            )

    def unregister_provider(self, provider_id: str) -> None:
        """Remove a previously registered provider and all its models.

        No-op if the provider is not found.  Built-in providers can be removed
        this way too, but will come back on the next process restart.

        Example::

            tau.unregister_provider("my-llm")
        """
        from tau.inference.api.text.service import TextLLM

        TextLLM._builtin_providers().unregister(provider_id)
        TextLLM._builtin_models().unregister_by_provider(provider_id)

    # ── Themes ────────────────────────────────────────────────────────────────

    def register_theme(self, name: str, theme_or_factory: Any) -> None:
        """Register a named theme.

        Accepts either a ``LayoutTheme`` instance or a zero-arg factory
        function that returns one (preferred for lazy loading).

        After registration the theme appears in the ``/theme`` picker.
        """
        from tau.themes.registry import theme_registry

        theme_registry.register(name, theme_or_factory)

    # ── Settings UI ───────────────────────────────────────────────────────────

    def register_settings(
        self,
        items: list,
        title: str = "",
        on_change: Callable[[str, str], None] | None = None,
    ) -> None:
        """Expose extension settings in the /settings panel as a nested sub-panel.

        ``items`` is a list of ``SettingItem`` objects (from
        ``tau.modes.interactive.components.settings_selector``).  ``on_change(key, value)`` is
        called whenever the user changes a value in the sub-panel; use it to
        persist changes via ``tau.settings``.

        Example::

            from dataclasses import dataclass
            from tau.modes.interactive.components.settings_selector import SettingItem

            def register(tau):
                def on_change(key, value):
                    tau.settings.set_extension_config_key(tau.cwd, __file__, key, value)

                tau.register_settings([
                    SettingItem(id="verbose", label="Verbose", current_value="false",
                                values=["false", "true"]),
                    SettingItem(id="timeout_ms", label="Timeout (ms)",
                                current_value=str(tau.config.get("timeout_ms", 5000)),
                                text_input=True),
                ], title="My Extension", on_change=on_change)
        """
        reg = ExtensionSettingsRegistration(
            title=title or self._extension.path.split("/")[-1],
            items=items,
            on_change=on_change or (lambda k, v: None),
        )
        self._extension.settings_registrations.append(reg)

    # ── Flags ─────────────────────────────────────────────────────────────────

    def register_flag(
        self,
        name: str,
        type: Literal["bool", "str", "int"] = "str",
        default: bool | str | int | None = None,
        description: str | None = None,
        env: str | None = None,
    ) -> None:
        """Declare a configuration flag backed by an environment variable.

        ``env`` is the environment variable name to read (e.g. ``"MY_EXT_VERBOSE"``).
        ``default`` is returned when the env var is absent or unset.

        Example::

            tau.register_flag("verbose", type="bool", env="TAU_VERBOSE", default=False)
        """
        self._flags[name] = FlagRegistration(
            name=name,
            type=type,
            default=default,
            description=description,
            env=env,
        )

    def get_flag(self, name: str) -> bool | str | int | None:
        """Return the current value for a registered flag.

        Reads from the declared env var first; falls back to the default.
        Returns None if the flag has not been registered.
        """
        reg = self._flags.get(name)
        if reg is None:
            return None
        if reg.env:
            raw = os.environ.get(reg.env)
            if raw is not None:
                if reg.type == "bool":
                    return raw.strip().lower() in ("1", "true", "yes")
                if reg.type == "int":
                    try:
                        return int(raw)
                    except ValueError:
                        pass
                return raw
        return reg.default

    # ── Session metadata ──────────────────────────────────────────────────────

    def set_session_name(self, name: str) -> None:
        """Set the display name for the current session (shown in the session picker)."""
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        sm = getattr(runtime, "session_manager", None)
        if sm is not None:
            sm.append_session_info(name)

    def get_session_name(self) -> str | None:
        """Return the current session display name, or None if not set."""
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return None
        sm = getattr(runtime, "session_manager", None)
        return sm.get_session_name() if sm is not None else None

    def set_label(self, entry_id: str, label: str | None = None) -> None:
        """Set or clear a label on an entry.

        Labels are user-visible markers useful for bookmarking important
        branch points.  Pass ``None`` or an empty string to clear the label.
        """
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        sm = getattr(runtime, "session_manager", None)
        if sm is not None:
            sm.append_label_change(entry_id, label or None)

    def append_entry(self, custom_type: str, data: Any = None) -> str | None:
        """Persist arbitrary data into the session's JSONL log.

        The entry survives restarts and can be retrieved via
        ``ctx.get_entries()`` on the next load.  Use ``custom_type`` as a
        namespace so different extensions don't collide.

        Returns the new entry's ID, or ``None`` if no session is active.

        Example::

            # Store state that survives restart
            entry_id = tau.append_entry("my-ext:checkpoint", {"step": 3})

            # Read it back in session_start
            @tau.on("session_start")
            async def on_start(event, ctx):
                for entry in ctx.get_entries():
                    if entry.type == "custom" and entry.custom_type == "my-ext:checkpoint":
                        resume_from(entry.data["step"])
        """
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return None
        sm = getattr(runtime, "session_manager", None)
        if sm is None:
            return None
        return sm.append_custom_info(custom_type, data)

    # ── Commands ──────────────────────────────────────────────────────────────

    def get_commands(self) -> list[dict]:
        """Return a list of dicts describing every registered slash command.

        Each dict contains ``name`` and ``description`` keys.
        """
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return []
        cmds = getattr(getattr(runtime, "commands", None), "_registry", {})
        return [{"name": k, "description": getattr(v, "description", "")} for k, v in cmds.items()]

    # ── Active tools ──────────────────────────────────────────────────────────

    def get_active_tools(self) -> list[str]:
        """Return the names of all tools currently enabled for the agent."""
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return []
        agent = getattr(runtime, "agent", None)
        if agent is None:
            return []
        engine = getattr(agent, "_engine", None)
        if engine is None:
            return []
        return [t.name for t in getattr(engine, "tools", [])]

    def get_all_tools(self) -> list[dict]:
        """Return all registered tools (both active and currently disabled).

        Each dict contains:
          ``name``              — the tool name used in LLM tool calls.
          ``description``       — the description shown to the model.
          ``parameters``        — the JSON Schema for the tool's arguments
                                  (``None`` if the tool exposes no schema).
          ``prompt_guidelines`` — extra system-prompt guidance for the tool, or
                                  ``None``.

        Use this to introspect another extension's (or a built-in's) tool
        contract — e.g. to wrap it, validate against it, or surface it in a UI.
        """
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return []
        ctx = getattr(runtime, "_context", None)
        if ctx is None:
            return []
        registry = getattr(ctx, "tool_registry", None)
        if registry is None:
            return []

        def _schema(t: Any) -> dict | None:
            schema = getattr(t, "schema", None)
            getter = getattr(schema, "model_json_schema", None)
            if getter is None:
                return None
            try:
                return getter()
            except Exception:
                return None

        return [
            {
                "name": t.name,
                "description": getattr(t, "description", ""),
                "parameters": _schema(t),
                "prompt_guidelines": getattr(t, "prompt_guidelines", None),
            }
            for t in registry.list()
        ]

    def set_active_tools(self, tool_names: list[str]) -> None:
        """Restrict the agent to only the named tools (or re-enable all if the list is empty)."""
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        agent = getattr(runtime, "agent", None)
        if agent is None:
            return
        engine = getattr(agent, "_engine", None)
        if engine is None:
            return
        ctx = getattr(runtime, "_context", None)
        registry = getattr(ctx, "tool_registry", None)
        if registry is None:
            return
        all_tools = registry.list()
        filtered = [t for t in all_tools if not tool_names or t.name in tool_names]
        # Keep the advertised list and the execution lookup in sync, mirroring
        # ToolRegistry.sync_to_engine.
        engine.tools = filtered
        engine._tools = {t.name: t for t in filtered}

    # ── Model / thinking ──────────────────────────────────────────────────────

    def get_thinking_level(self) -> str:
        """Return the current thinking level identifier (e.g. ``'low'``, ``'high'``)."""
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return "none"
        agent = getattr(runtime, "agent", None)
        if agent is None:
            return "none"
        engine = getattr(agent, "_engine", None)
        if engine is None:
            return "none"
        # The live knob is the LLM request options; ``None`` there means "off"
        # (see the /model command's thinking-level handling).
        level = engine.llm.api.options.thinking_level
        if level is None:
            return "off"
        return level.value if hasattr(level, "value") else str(level)

    def set_thinking_level(self, level: str) -> None:
        """Set the thinking level.  Accepts any ThinkingLevel string value."""
        import asyncio

        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        agent = getattr(runtime, "agent", None)
        if agent is None:
            return
        engine = getattr(agent, "_engine", None)
        if engine is None:
            return
        from tau.inference.types import ThinkingLevel

        try:
            tl = ThinkingLevel(level)
        except ValueError:
            return
        # The live knob is the LLM request options; ``Off`` is stored as None
        # (see the /model command's thinking-level handling).
        engine.llm.api.options.thinking_level = None if tl == ThinkingLevel.Off else tl
        from tau.hooks.types import ThinkingLevelSelectEvent

        hooks = getattr(runtime, "hooks", None)
        if hooks is not None:
            asyncio.ensure_future(hooks.emit(ThinkingLevelSelectEvent(level=tl)))

    def set_model(self, model_id: str, provider: str | None = None) -> None:
        """Switch the active model, emitting ``model_select``.

        Fire-and-forget: schedules the swap and returns immediately, so it is
        safe to call from synchronous handlers (e.g. a shortcut or settings
        ``on_change``). For an awaitable result use ``ctx.set_model()`` inside an
        event or command handler instead.

        ``provider`` disambiguates when the same model id is served by more than
        one provider; omit it to use the default.
        """
        import asyncio

        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        set_fn = getattr(runtime, "set_model", None)
        if set_fn is not None:
            asyncio.ensure_future(set_fn(model_id, provider))

    # ── Prompt ────────────────────────────────────────────────────────────────

    def append_prompt(self, text: str) -> None:
        """Append text verbatim to the end of the system prompt."""
        if text.strip():
            self._extension.prompt_appends.append(text)

    # ── Reload ────────────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Reload all extensions, applying any settings changes to the live session.

        Re-reads settings.json and re-runs every extension's ``register(tau)`` with
        the fresh config, swapping tools/commands/prompt in place — no restart or
        new session needed. Schedules asynchronously and returns immediately, so it
        is safe to call from a synchronous handler such as a settings ``on_change``.

        Requests made while an extension callback or agent turn is active are
        deferred to the next safe boundary. Extensions that hold external
        resources should release them from ``extension_unload`` or ``runtime_stop``.
        """
        import asyncio

        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        reload_fn = getattr(runtime, "reload_extensions", None)
        if reload_fn is not None:
            asyncio.ensure_future(reload_fn())

    # ── Shell exec ────────────────────────────────────────────────────────────

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        cwd: Path | str | None = None,
    ) -> ExecResult:
        """Run a shell command and return ``ExecResult(stdout, stderr, code)``.

        Example::

            result = await tau.exec("git", ["status", "--porcelain"])
            if result.code == 0:
                print(result.stdout)
        """
        import asyncio as _asyncio
        from asyncio.subprocess import PIPE

        resolved_cwd = str(cwd) if cwd else str(self._cwd)
        proc = await _asyncio.create_subprocess_exec(
            cmd,
            *(args or []),
            stdin=_asyncio.subprocess.DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
            cwd=resolved_cwd,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return ExecResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            code=proc.returncode,
        )

    # ── Built-in tool factories ───────────────────────────────────────────────

    def get_builtin_tool(self, name: str) -> Any:
        """Return a fresh instance of a built-in tool by name for execution delegation.

        Allows extensions to override a built-in's rendering while keeping the
        original execution logic::

            original = tau.get_builtin_tool("read")

            def render_result(content, opts):
                lines = content.splitlines()
                return [f"{len(lines)} lines" + (" [error]" if opts.is_error else "")]

            tau.register_tool(Tool(
                name="read",
                description=original.description,
                schema=original.schema,
                kind=original.kind,
                render_result=render_result,
                execute=original.execute,   # delegate to the original
            ))

        Returns None if no built-in with that name exists.
        Supported names: ``read``, ``write``, ``edit``, ``terminal``, ``glob``, ``grep``, ``ls``.
        """
        from tau.builtins.tools import (
            create_edit_tool,
            create_glob_tool,
            create_grep_tool,
            create_ls_tool,
            create_read_tool,
            create_terminal_tool,
            create_write_tool,
        )

        _factories = {
            "read": create_read_tool,
            "write": create_write_tool,
            "edit": create_edit_tool,
            "terminal": create_terminal_tool,
            "glob": create_glob_tool,
            "grep": create_grep_tool,
            "ls": create_ls_tool,
        }
        factory = _factories.get(name)
        return factory() if factory is not None else None

    # ── Read-only info ────────────────────────────────────────────────────────

    @property
    def config(self) -> dict:
        """Per-extension settings dict from ``extensions.list[].settings`` in settings.json.

        Empty dict when no matching entry exists or no ``settings`` key is set.
        Read-only at registration time — store derived values in local variables.
        """
        return self._extension.config

    @property
    def settings(self) -> SettingsManager:
        """Access the settings manager."""
        return self._settings

    @property
    def cwd(self) -> Path:
        """Working directory at session startup."""
        return self._cwd

    @property
    def model_id(self) -> str:
        """Active model identifier, e.g. ``'claude-sonnet-4-6'``."""
        return self._llm.model.id

    @property
    def provider_id(self) -> str:
        """Active provider identifier, e.g. ``'anthropic'``."""
        return self._llm.provider_id
