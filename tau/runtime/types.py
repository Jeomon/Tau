from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from tau.agent.prompt.builder import build_prompt
from tau.agent.prompt.types import PromptOptions
from tau.agent.service import Agent
from tau.agent.types import AgentConfig
from tau.builtins.tools import TOOLS
from tau.engine.service import Engine
from tau.extensions.api import _RuntimeRef
from tau.extensions.loader import ExtensionLoader
from tau.extensions.runtime import ExtensionRuntime
from tau.hooks.service import Hooks
from tau.inference.api.text.service import TextLLM as LLM
from tau.packages.utils import add_site_packages_path
from tau.session.manager import SessionManager
from tau.settings.manager import SettingsManager
from tau.settings.paths import get_config_dir, get_extensions_dir
from tau.tool.registry import ToolRegistry
from tau.tool.types import Tool

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_PROVIDER = "anthropic"


def _is_project_package(settings_manager: SettingsManager, name: str) -> bool:
    """Return True if the package is project-scoped (in project settings)."""
    project_pkgs = settings_manager.get_packages(local=True)
    return any(p.name == name for p in project_pkgs)


class RuntimeConfig(BaseModel):
    """Immutable configuration snapshot passed to RuntimeContext.create()."""

    model_config = {"arbitrary_types_allowed": True}

    cwd: Path
    config_dir: Path | None = None

    # LLM
    model_id: str | None = None
    provider: str | None = None

    # Session
    session_file: Path | None = None
    persist_session: bool = True
    resume: bool = False

    # Run mode
    mode: str = "interactive"

    # Tools & prompt
    tools: list[Tool] = Field(default_factory=list)
    system_prompt: str = ""
    disable_context_files: bool = False

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
    ) -> None:
        self.agent = agent
        self.llm = llm
        self.engine = engine
        self.session_manager = session_manager
        self.settings_manager = settings_manager
        self.hooks: Hooks = hooks or agent.hooks
        self.ext_runtime: ExtensionRuntime | None = ext_runtime
        self.tool_registry: ToolRegistry = tool_registry or ToolRegistry()

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

            if not has_project_trust_inputs(cwd):
                project_trusted = True
            elif config.project_trusted is not None:
                project_trusted = config.project_trusted
            else:
                # Load global settings first to read project_trust policy
                _global_sm = SettingsManager.create(
                    cwd=cwd, config_dir=config_dir, project_trusted=False
                )
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
            settings_manager = SettingsManager.create(
                cwd, config_dir=config_dir, project_trusted=project_trusted
            )

        # ── LLM ───────────────────────────────────────────────────────────────
        text_ref = settings_manager.get_model_ref("text")
        model_id = config.model_id or (text_ref.id if text_ref else None) or _DEFAULT_MODEL
        provider = config.provider or (text_ref.provider if text_ref else None) or _DEFAULT_PROVIDER
        llm = LLM(model_id=model_id, provider=provider)
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

        # ── Session manager ───────────────────────────────────────────────────
        # Don't create the session directory until trust is granted. When trust
        # is pending (policy="ask", no prior decision) the TrustScreen will call
        # session_manager.enable_persist() after the user approves.
        _persist = config.persist_session and not _trust_pending
        if config.resume and not config.session_file and _persist:
            session_manager = SessionManager.continue_recent(cwd)
        else:
            session_manager = SessionManager(
                cwd=cwd,
                session_file=config.session_file,
                persist=_persist,
            )

        # ── Shared hook bus ───────────────────────────────────────────────────
        hooks = hooks or Hooks()

        # ── Extensions ────────────────────────────────────────────────────────
        # Only load on first session; on session switch the caller passes ext_runtime.
        base_tools: list[Tool] = list(TOOLS) + list(config.tools)

        if ext_runtime is None:
            from tau.hooks.runtime import RuntimeStartEvent
            from tau.settings.paths import get_builtins_dir

            builtins_ext_dir = get_builtins_dir() / "extensions"
            runtime_ref = _RuntimeRef()

            # Earliest lifecycle signal — hooks bus exists, nothing loaded yet.
            # Reaches core/manual subscribers only (no extensions registered yet);
            # opens the runtime_start → runtime_ready → runtime_stop bracket.
            await hooks.emit(RuntimeStartEvent())

            if settings_manager.is_extensions_enabled():
                global_ext_dir = get_extensions_dir()
                project_ext_dir = get_extensions_dir(cwd)

                disabled_stems = set()
                entry_configs = {}
                extra_entries = []

                entries = settings_manager.get_all_extension_entries()

                for e in entries:
                    stem = Path(e.path).stem
                    if not e.enabled:
                        disabled_stems.add(stem)
                    else:
                        entry_configs[stem] = e.settings or {}
                        extra_entries.append(e)

                extra_sources: dict[str, str] = {}

                # Discover extension files contributed by installed packages
                from tau.packages.manager import PackageManager
                from tau.settings.paths import get_packages_venv
                from tau.settings.types import ExtensionEntry as _ExtEntry

                pkg_entries = settings_manager.get_all_packages()
                
                if pkg_entries:
                    for _scope_local in (False, True):
                        _venv_dir = get_packages_venv(cwd if _scope_local else None)
                        _pkg_mgr = PackageManager(_venv_dir)
                        add_site_packages_path(_pkg_mgr.site_packages())

                    for pkg in pkg_entries:
                        if not pkg.enabled:
                            continue
                        _venv_dir = get_packages_venv(
                            cwd if _is_project_package(settings_manager, pkg.name) else None
                        )
                        _pkg_mgr = PackageManager(_venv_dir)

                        for ext_file in _pkg_mgr.find_extension_files(pkg.name, pkg.installed_path):
                            extra_entries.append(_ExtEntry(path=str(ext_file), name=pkg.name))
                            extra_sources[str(ext_file)] = "package"

                el = ExtensionLoader(
                    builtins_dir=builtins_ext_dir,
                    project_dir=project_ext_dir,
                    global_dir=global_ext_dir,
                    extra_entries=extra_entries,
                    extra_sources=extra_sources,
                    disabled_stems=disabled_stems,
                    entry_configs=entry_configs,
                    llm=llm,
                    settings=settings_manager,
                    cwd=cwd,
                    runtime_ref=runtime_ref,
                )
            else:
                # Extensions disabled — load builtins only.
                el = ExtensionLoader(
                    builtins_dir=builtins_ext_dir,
                    llm=llm,
                    settings=settings_manager,
                    cwd=cwd,
                    runtime_ref=runtime_ref,
                )
            load_result = await el.load()
            ext_runtime = ExtensionRuntime(load_result, hooks, runtime_ref)

        assert ext_runtime is not None
        # Collect tools and prompt appends contributed by extensions
        extra_appends = ext_runtime.get_prompt_appends()

        # ── Tool registry ─────────────────────────────────────────────────────
        tool_registry = ToolRegistry()
        for tool in base_tools:
            source = "builtin" if tool in list(TOOLS) else "runtime"
            tool_registry.register(tool, source=source)

        for tool in ext_runtime.get_tools():
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

        # ── Skills ────────────────────────────────────────────────────────────
        from tau.skills.registry import skill_registry

        skill_registry.load_external(cwd=cwd)
        skills = skill_registry.list()

        # ── System prompt ─────────────────────────────────────────────────────
        system_prompt = config.system_prompt or build_prompt(
            PromptOptions(
                cwd=cwd,
                tools=all_tools,
                extra_appends=extra_appends,
                skills=skills,
                disable_context_files=config.disable_context_files,
                project_trusted=project_trusted,
            )
        )

        # ── Agent config ──────────────────────────────────────────────────────
        agent_config = AgentConfig(
            cwd=cwd,
            system_prompt=system_prompt,
            model=llm.model,
            # input_limit (not the total window) is the budget compaction/overflow key off.
            context_window=llm.model.input_limit or 200_000,
        )

        # ── Agent ─────────────────────────────────────────────────────────────
        agent = Agent(
            engine=engine,
            session_manager=session_manager,
            config=agent_config,
            hooks=hooks,
        )

        return cls(
            agent=agent,
            llm=llm,
            engine=engine,
            session_manager=session_manager,
            settings_manager=settings_manager,
            hooks=hooks,
            ext_runtime=ext_runtime,
            tool_registry=tool_registry,
        )
