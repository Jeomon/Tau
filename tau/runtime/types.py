from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tau.agent.prompt.builder import _git_status, build_prompt
from tau.agent.prompt.types import PromptOptions
from tau.agent.service import Agent
from tau.agent.types import AgentConfig
from tau.builtins.tools import TOOLS
from tau.engine.service import Engine
from tau.extensions.api import ExtensionError, ExtensionFactory, _RuntimeRef
from tau.extensions.runtime import ExtensionRuntime
from tau.hooks.service import Hooks
from tau.inference.api.text.service import TextLLM as LLM
from tau.message.types import AgentMessage, UserMessage
from tau.resources.loader import DefaultResourceLoader, ResourceLoader
from tau.resources.types import ResourceContext, ResourceDiagnostic, ResourceSnapshot
from tau.runtime.dependencies import (
    LLMFactoryContext,
    RuntimeDependencies,
    SessionManagerFactoryContext,
    SettingsFactoryContext,
)
from tau.session.compaction import CompactionSettings, validated_compaction_settings
from tau.session.manager import SessionManager
from tau.settings.manager import SettingsManager
from tau.settings.paths import get_config_dir
from tau.tool.registry import ToolRegistry
from tau.tool.types import Tool
from tau.utils import timing

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_PROVIDER = "anthropic"


@dataclass(frozen=True)
class RuntimeStartupResult:
    """Structured outcome of creating a runtime."""

    runtime: Runtime
    resource_diagnostics: tuple[ResourceDiagnostic, ...]
    extension_errors: tuple[ExtensionError, ...]
    requested_model_id: str
    requested_provider_id: str | None
    selected_model_id: str
    selected_provider_id: str
    model_fallback_reason: str | None = None

    @property
    def has_issues(self) -> bool:
        """Return whether startup produced any resource or extension issue."""
        return bool(self.resource_diagnostics or self.extension_errors)


class RuntimeConfig(BaseModel):
    """Immutable configuration snapshot passed to RuntimeContext.create()."""

    model_config = {"arbitrary_types_allowed": True}

    cwd: Path
    config_dir: Path | None = None

    # LLM
    model_id: str | None = None
    provider: str | None = None
    base_url: str | None = None  # temporary override for the resolved provider's base URL

    # Session
    session_file: Path | None = None
    session_dir: Path | None = None
    persist_session: bool = True
    resume: bool = False

    # Startup conversation seed
    initial_messages: list[AgentMessage] = Field(default_factory=list)
    initial_prompt: str | None = None
    initial_images: list[Any] = Field(default_factory=list)
    initial_audio: list[str | bytes] = Field(default_factory=list)
    initial_video: list[str | bytes] = Field(default_factory=list)

    # Run mode
    mode: str = "interactive"

    # Tools & prompt
    tools: list[Tool] = Field(default_factory=list)
    tool_allowlist: set[str] | None = None
    exclude_tools: set[str] = Field(default_factory=set)
    system_prompt: str = ""
    disable_context_files: bool = False
    resource_loader: ResourceLoader | None = None
    extension_factories: list[ExtensionFactory] = Field(default_factory=list)
    dependencies: RuntimeDependencies = Field(default_factory=RuntimeDependencies)

    # Trust
    project_trusted: bool | None = None  # None = auto-detect from trust store


class RuntimeContext:
    """
    Constructs and owns all dependencies for one Agent session.

    Usage:
        ctx = await RuntimeContext.create(config)
        agent = ctx.agent
        await agent.invoke("hello")
    """

    def __init__(
        self,
        agent: Agent,
        llm: LLM,
        engine: Engine,
        session_manager: SessionManager,
        settings_manager: SettingsManager | None = None,
        hooks: Hooks | None = None,
        ext_runtime: ExtensionRuntime | None = None,
        tool_registry: ToolRegistry | None = None,
        resource_loader: ResourceLoader | None = None,
        resource_snapshot: ResourceSnapshot | None = None,
        project_trusted: bool = False,
        requested_model_id: str = "",
        requested_provider_id: str | None = None,
    ) -> None:
        self.agent = agent
        self.llm = llm
        self.engine = engine
        self.session_manager = session_manager
        self.settings_manager = settings_manager
        self.hooks: Hooks = hooks or agent.hooks
        self.ext_runtime: ExtensionRuntime | None = ext_runtime
        self.tool_registry: ToolRegistry = tool_registry or ToolRegistry()
        self.resource_loader = resource_loader
        self.resource_snapshot = resource_snapshot
        self.project_trusted = project_trusted
        self.requested_model_id = requested_model_id
        self.requested_provider_id = requested_provider_id

    @classmethod
    async def create(
        cls,
        config: RuntimeConfig,
        settings_manager: SettingsManager | None = None,
        hooks: Hooks | None = None,
        ext_runtime: ExtensionRuntime | None = None,
    ) -> RuntimeContext:
        """Bootstrap every dependency from config and return a fully wired context."""
        cwd = config.cwd.resolve()
        config_dir = (config.config_dir or get_config_dir()).resolve()

        # Determine project trust status (needed for context file loading)
        project_trusted: bool = (
            config.project_trusted if config.project_trusted is not None else False
        )

        # ── Settings ──────────────────────────────────────────────────────────
        _trust_pending = False
        if settings_manager is None:
            from tau.trust.manager import has_project_trust_inputs, trust_store

            # Set when the default (non-injected) path builds a SettingsManager
            # solely to read the project_trust policy — reused below instead of
            # constructing a second one, since SettingsManager.from_storage()
            # unconditionally reads BOTH global and project settings from disk
            # regardless of project_trusted (that flag only gates whether
            # project_settings is exposed, not whether it's read) — so building
            # a fresh instance here would re-read the same two files a model
            # already just read.
            _trust_probe_sm: SettingsManager | None = None
            if not has_project_trust_inputs(cwd):
                project_trusted = True
            elif config.project_trusted is not None:
                project_trusted = config.project_trusted
            else:
                # Load global settings first to read project_trust policy
                settings_context = SettingsFactoryContext(
                    cwd=cwd,
                    config_dir=config_dir,
                    project_trusted=False,
                )
                if config.dependencies.settings is not None:
                    _global_sm = config.dependencies.settings(settings_context)
                else:
                    _global_sm = SettingsManager.create(
                        cwd=cwd,
                        config_dir=config_dir,
                        project_trusted=False,
                    )
                    _trust_probe_sm = _global_sm
                policy = _global_sm.get_project_trust()
                match policy:
                    case "always":
                        project_trusted = True
                    case "never":
                        project_trusted = False
                    case "ask" | _:
                        stored = trust_store.get(cwd)
                        project_trusted = stored if stored is not None else False
                        _trust_pending = stored is None  # no prior decision → TrustScreen will show
            if _trust_probe_sm is not None:
                _trust_probe_sm.set_project_trusted(project_trusted)
                settings_manager = _trust_probe_sm
            else:
                settings_context = SettingsFactoryContext(
                    cwd=cwd,
                    config_dir=config_dir,
                    project_trusted=project_trusted,
                )
                settings_manager = (
                    config.dependencies.settings(settings_context)
                    if config.dependencies.settings is not None
                    else SettingsManager.create(
                        cwd,
                        config_dir=config_dir,
                        project_trusted=project_trusted,
                    )
                )
        timing.mark("settings")

        # Kick off the git-status snapshot now (needed later for the system prompt)
        # so its subprocess calls run in a background thread concurrently with the
        # rest of startup (settings, extension loading, resource discovery) instead
        # of blocking the event loop right before the prompt is built.
        git_task: asyncio.Task[str] | None = (
            asyncio.create_task(asyncio.to_thread(_git_status, cwd))
            if project_trusted and not config.system_prompt
            else None
        )

        # ── LLM ───────────────────────────────────────────────────────────────
        text_ref = settings_manager.get_model_ref("text")
        model_id = config.model_id or (text_ref.id if text_ref else None) or _DEFAULT_MODEL
        provider = config.provider or (text_ref.provider if text_ref else None) or _DEFAULT_PROVIDER
        llm_context = LLMFactoryContext(
            model_id=model_id,
            provider=provider,
            settings=settings_manager,
        )
        llm = (
            config.dependencies.llm(llm_context)
            if config.dependencies.llm is not None
            else LLM(model_id=model_id, provider=provider)
        )
        if config.base_url:
            llm.api.options.base_url = config.base_url
        from datetime import timedelta

        llm.api.options.timeout = timedelta(
            milliseconds=settings_manager.get_http_idle_timeout_ms()
        )
        if settings_manager.is_retry_enabled():
            llm.api.options.max_retries = settings_manager.get_retry_max_retries()
            llm.api.options.retry_base_delay_ms = settings_manager.get_retry_base_delay_ms()
        else:
            llm.api.options.max_retries = 0
        if llm.model.thinking:
            llm.api.options.thinking_level = (
                settings_manager.get_thinking_level() or llm.model.thinking_level
            )
        timing.mark("llm")

        # ── Session manager ───────────────────────────────────────────────────
        # Don't create the session directory until trust is granted. When trust
        # is pending (policy="ask", no prior decision) the TrustScreen will call
        # session_manager.enable_persist() after the user approves.
        _persist = config.persist_session and not _trust_pending
        session_dir = config.session_dir or settings_manager.get_session_dir()
        session_context = SessionManagerFactoryContext(
            cwd=cwd,
            session_dir=session_dir,
            session_file=config.session_file,
            persist=_persist,
            resume=config.resume,
        )
        if config.dependencies.session_manager is not None:
            session_manager = config.dependencies.session_manager(session_context)
        elif config.resume and not config.session_file and _persist:
            session_manager = SessionManager.continue_recent(cwd, session_dir=session_dir)
        else:
            session_manager = SessionManager(
                cwd=cwd,
                session_dir=session_dir,
                session_file=config.session_file,
                persist=_persist,
            )
        # Record the starting model/thinking level for a genuinely new session
        # (leaf_id is only None before any entry has ever been appended — a
        # resumed session already has its own history to reconstruct from).
        # Without this, build_session_context() finds no ModelChangeEntry/
        # ThinkingLevelChangeEntry to scan when the user never explicitly
        # switched either mid-session, and reconstructs model_id=None /
        # thinking_level=Off on resume — silently losing whatever was
        # actually used throughout the session.
        if session_manager.leaf_id is None:
            model_id_attr = getattr(llm.model, "id", None)
            provider_id_attr = getattr(llm, "provider_id", None)
            if model_id_attr is not None and provider_id_attr is not None:
                session_manager.append_model_change(model_id_attr, provider_id_attr)
            if llm.model.thinking and llm.api.options.thinking_level is not None:
                session_manager.append_thinking_level_change(llm.api.options.thinking_level)
        _seed_initial_messages(session_manager, config)
        timing.mark("session_manager")

        # ── Shared hook bus ───────────────────────────────────────────────────
        hooks = hooks or (
            config.dependencies.hooks() if config.dependencies.hooks is not None else Hooks()
        )

        # ── Extensions ────────────────────────────────────────────────────────
        # Only load on first session; on session switch the caller passes ext_runtime.
        def tool_enabled(tool: Tool) -> bool:
            return (
                config.tool_allowlist is None or tool.name in config.tool_allowlist
            ) and tool.name not in config.exclude_tools

        base_tools = [tool for tool in [*TOOLS, *config.tools] if tool_enabled(tool)]

        from tau.hooks.runtime import RuntimeStartEvent

        if ext_runtime is None:
            # Earliest lifecycle signal — core/manual subscribers only.
            await hooks.emit(RuntimeStartEvent())

        resource_loader = config.resource_loader or DefaultResourceLoader()
        resource_context = ResourceContext(
            cwd=cwd,
            settings=settings_manager,
            hooks=hooks,
            load_context_files=not config.disable_context_files and project_trusted,
        )
        resources = await resource_loader.discover(resource_context)

        # Apply skills/prompts/themes from the resource snapshot before loading
        # extensions: ExtensionLoader.load() additively registers any skills an
        # extension's manifest.json declares, and apply_registries()'s reload()
        # clears the whole skill registry before rebuilding it from the
        # snapshot alone — running it first (not after, like the naive order
        # would) means those declarations survive instead of being wiped.
        resource_loader.apply_registries(resources, context=resource_context)
        timing.mark("resources")

        if ext_runtime is None:
            runtime_ref = _RuntimeRef()
            el = resource_loader.create_extension_loader(
                resources,
                context=resource_context,
                llm=llm,
                runtime_ref=runtime_ref,
            )
            load_result = await el.load()
            if config.extension_factories:
                from tau.extensions.loader import load_inline_extensions

                inline_result = await load_inline_extensions(
                    config.extension_factories,
                    llm=llm,
                    settings=settings_manager,
                    cwd=cwd,
                    runtime_ref=runtime_ref,
                )
                load_result.extensions.extend(inline_result.extensions)
                load_result.errors.extend(inline_result.errors)
            ext_runtime = ExtensionRuntime(load_result, hooks, runtime_ref)

        assert ext_runtime is not None
        timing.mark("extensions")
        # Collect tools and prompt appends contributed by extensions
        extra_appends = ext_runtime.get_prompt_appends()

        # ── Tool registry ─────────────────────────────────────────────────────
        tool_registry = (
            config.dependencies.tool_registry()
            if config.dependencies.tool_registry is not None
            else ToolRegistry()
        )
        for tool in base_tools:
            source = "builtin" if tool in list(TOOLS) else "runtime"
            tool_registry.register(tool, source=source)

        for tool in ext_runtime.get_tools():
            if not tool_enabled(tool):
                continue
            tool_registry.register(tool, source="extension")

        all_tools: list[Tool] = tool_registry.list()

        # ── Engine ────────────────────────────────────────────────────────────
        engine = Engine(
            cwd=cwd,
            llm=llm,
            tools=all_tools,
            hooks=hooks,
            settings=settings_manager,
        )

        # ── Skills, prompts, and themes ──────────────────────────────────────
        from tau.skills.registry import skill_registry

        skills = skill_registry.list()

        # ── System prompt ─────────────────────────────────────────────────────
        git_snapshot = await git_task if git_task is not None else None
        system_prompt = (
            config.system_prompt
            or resources.system_prompt
            or build_prompt(
                PromptOptions(
                    cwd=cwd,
                    tools=all_tools,
                    extra_appends=extra_appends,
                    skills=skills,
                    disable_context_files=config.disable_context_files,
                    project_trusted=project_trusted,
                    context_files=resources.context_files,
                    git_snapshot=git_snapshot,
                )
            )
        )

        # ── Agent config ──────────────────────────────────────────────────────
        context_window = llm.model.input_limit or 200_000
        compaction_settings = validated_compaction_settings(
            CompactionSettings(
                enabled=settings_manager.is_compaction_enabled(),
                reserve_tokens=settings_manager.get_compaction_reserve_tokens(),
                keep_recent_tokens=settings_manager.get_compaction_keep_recent_tokens(),
            ),
            context_window,
        )
        agent_config = AgentConfig(
            cwd=cwd,
            system_prompt=system_prompt,
            model=llm.model,
            # input_limit (not the total window) is the budget compaction/overflow key off.
            context_window=context_window,
            compaction=compaction_settings,
        )

        # ── Agent ─────────────────────────────────────────────────────────────
        agent = Agent(
            engine=engine,
            session_manager=session_manager,
            config=agent_config,
            hooks=hooks,
        )
        timing.mark("agent")

        return cls(
            agent=agent,
            llm=llm,
            engine=engine,
            session_manager=session_manager,
            settings_manager=settings_manager,
            hooks=hooks,
            ext_runtime=ext_runtime,
            tool_registry=tool_registry,
            resource_loader=resource_loader,
            resource_snapshot=resources,
            project_trusted=project_trusted,
            requested_model_id=model_id,
            requested_provider_id=provider,
        )


def _seed_initial_messages(
    session_manager: SessionManager,
    config: RuntimeConfig,
) -> None:
    """Append configured startup history and media to the active session."""
    for message in config.initial_messages:
        session_manager.append_message(message)

    if (
        config.initial_prompt is not None
        or config.initial_images
        or config.initial_audio
        or config.initial_video
    ):
        session_manager.append_message(
            UserMessage.with_media(
                config.initial_prompt or "",
                images=config.initial_images,
                audio=config.initial_audio,
                video=config.initial_video,
            )
        )
