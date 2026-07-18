from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from tau.agent.service import Agent
from tau.agent.types import AgentPhase, PromptOptions
from tau.commands.registry import CommandRegistry
from tau.commands.types import ParsedCommand
from tau.hooks.runtime import InputEvent, InputEventResult, RuntimeReadyEvent, RuntimeStopEvent
from tau.hooks.service import Handler, Unsubscribe
from tau.hooks.session import (
    BranchSummaryCancelledEvent,
    BranchSummaryEndEvent,
    BranchSummaryFailureEvent,
    BranchSummaryStartEvent,
    SessionBeforeForkEvent,
    SessionBeforeForkResult,
    SessionBeforeSwitchEvent,
    SessionBeforeSwitchReason,
    SessionBeforeSwitchResult,
    SessionBeforeTreeEvent,
    SessionBeforeTreeResult,
    SessionShutdownEvent,
    SessionShutdownReason,
    SessionStartEvent,
    SessionStartReason,
    SessionTreeEvent,
    TreePreparation,
)
from tau.resources.types import ResourceDiagnostic
from tau.runtime.types import RuntimeConfig, RuntimeContext, RuntimeStartupResult

if TYPE_CHECKING:
    from tau.modes.interactive.components.layout import Layout

_log = logging.getLogger(__name__)

# Caps how long shutdown waits for a cancelled background task to actually
# unwind. Exit should be near-instant; a task stuck in a non-cancellable
# blocking call (e.g. an HTTP client that doesn't honor cancellation cleanly)
# shouldn't be able to stall it — a timeout just means we stop waiting, not
# that cleanup failed.
_SHUTDOWN_TASK_TIMEOUT = 1.0
# `runtime_stop` handlers legitimately do multi-second subprocess teardown —
# e.g. the LSP extension's client.shutdown() already runs its own graceful
# shutdown-request timeout (2s) THEN a terminate/wait timeout (2s) before
# falling back to kill(), and the MCP extension disconnects servers
# sequentially. This cap only needs to rule out a truly hung handler (no
# internal timeout at all); cutting it too close would cancel an
# already-well-behaved handler before it reaches its own kill() fallback,
# orphaning the very subprocess it exists to reap.
#
# Also used to bound _emit_to_extension() (extension_unload/extension_reloaded,
# fired on every enable/disable/reload — not just process shutdown): those run
# under self._reload_lock, so a handler that hangs forever wedges every future
# reload/toggle attempt for the rest of the session, not just the current one.
_SHUTDOWN_HOOK_TIMEOUT = 10.0


class Runtime:
    """
    Orchestrates the session lifecycle: creation, switching, and forking
    on top of Agent and RuntimeContext.

    Usage:
        runtime = await Runtime.create(config)
        await runtime.invoke("explain this code")
    """

    def __init__(
        self,
        context: RuntimeContext,
        config: RuntimeConfig,
    ) -> None:
        self._context = context
        self._config = config
        self.commands = CommandRegistry(runtime=self)
        self._layout: Layout | None = None
        self._extension_ui_refresh: Callable[[], None] | None = None
        self._stopped: bool = False
        self._extension_generation: int = 0
        self._extension_callback_depth: int = 0
        self._extension_callbacks_idle = asyncio.Event()
        self._extension_callbacks_idle.set()
        self._reload_lock = asyncio.Lock()
        self._reload_pending: bool = False
        self._reload_task: asyncio.Task[None] | None = None
        self.version_check_task: asyncio.Task[str | None] | None = None
        self.telemetry_task: asyncio.Task[None] | None = None
        self.local_model_discovery_task: asyncio.Task[int] | None = None
        if context.agent is not None:
            context.agent._runtime = self
        # Bind runtime ref so (event, ctx) handlers resolve live state
        if context.ext_runtime is not None:
            context.ext_runtime.runtime_ref.runtime = self
        # Register extension commands into the command registry
        if context.ext_runtime is not None:
            for cmd in context.ext_runtime.get_commands():
                self.commands.register(cmd, source="extension")

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(cls, config: RuntimeConfig) -> Runtime:
        """Create a fully initialised Runtime from config and fire the session_start event."""
        context = await RuntimeContext.create(config=config)
        runtime_config = config.model_copy(
            update={
                "initial_messages": [],
                "initial_prompt": None,
                "initial_images": [],
                "initial_audio": [],
                "initial_video": [],
            }
        )
        runtime = cls(context=context, config=runtime_config)
        runtime._start_version_check()
        runtime._start_telemetry()
        runtime._start_local_model_discovery()
        await runtime._emit_session_start(SessionStartReason.Startup)
        # Runtime is now fully wired (engine, agent, tools, extensions) and the
        # session has started, but no mode-specific loop (TUI/print/rpc) has begun.
        # Extensions can hook `runtime_ready` to start background work from here.
        await context.hooks.emit(RuntimeReadyEvent())
        return runtime

    @classmethod
    async def create_with_result(cls, config: RuntimeConfig) -> RuntimeStartupResult:
        """Create a runtime and return its structured startup outcome."""
        runtime = await cls.create(config)
        context = runtime._context
        llm = context.llm
        selected_model_id = str(getattr(llm.model, "id", context.requested_model_id))
        selected_provider_id = str(getattr(llm, "provider_id", context.requested_provider_id or ""))
        fallback_reason = getattr(llm, "fallback_reason", None)
        if fallback_reason is None and selected_model_id != context.requested_model_id:
            fallback_reason = (
                f"Requested model '{context.requested_model_id}' resolved to '{selected_model_id}'"
            )
        elif (
            fallback_reason is None
            and context.requested_provider_id is not None
            and selected_provider_id != context.requested_provider_id
        ):
            fallback_reason = (
                f"Requested provider '{context.requested_provider_id}' resolved to "
                f"'{selected_provider_id}'"
            )

        extension_errors = (
            tuple(context.ext_runtime.errors) if context.ext_runtime is not None else ()
        )
        return RuntimeStartupResult(
            runtime=runtime,
            resource_diagnostics=runtime.resource_diagnostics,
            extension_errors=extension_errors,
            requested_model_id=context.requested_model_id,
            requested_provider_id=context.requested_provider_id,
            selected_model_id=selected_model_id,
            selected_provider_id=selected_provider_id,
            model_fallback_reason=fallback_reason,
        )

    def _start_version_check(self) -> None:
        from tau.settings.paths import get_app_version
        from tau.utils.version_check import check_for_new_version

        self.version_check_task = asyncio.ensure_future(check_for_new_version(get_app_version()))

    def _start_telemetry(self) -> None:
        """Start the best-effort version-only telemetry ping when enabled."""
        settings = self.settings_manager
        if settings is None or not settings.get_telemetry():
            return

        from tau.settings.paths import get_app_version
        from tau.telemetry import enable_exception_autocapture, report_install

        enable_exception_autocapture()
        self.telemetry_task = asyncio.ensure_future(report_install(get_app_version()))

    def _start_local_model_discovery(self) -> None:
        """Scan locally-running inference backends (Ollama, LM Studio, ...) for
        installed models, once, in the background, running every backend's scan
        in parallel.

        Local installs aren't in the static builtin catalog, so the model
        picker won't show them unless discovered at runtime. Best-effort:
        registers zero models for any backend that isn't installed/running,
        without blocking or failing the others. Results land in the shared
        model registry, which the `/model` picker reads fresh on every open —
        no further wiring needed to surface them in the TUI. Runs once per
        process — `Runtime.create` only fires at process startup, not on
        session reload/switch.
        """
        from tau.inference.model.local import register_all

        self.local_model_discovery_task = asyncio.ensure_future(register_all())

    # -------------------------------------------------------------------------
    # Public properties
    # -------------------------------------------------------------------------

    @property
    def agent(self) -> Agent | None:
        """Get the current agent instance."""
        return self._context.agent

    @property
    def hooks(self):
        """Get the hooks dispatcher."""
        return self._context.hooks

    @property
    def session_manager(self):
        """Get the session manager."""
        return self._context.session_manager

    @property
    def settings_manager(self):
        """Get the settings manager."""
        return self._context.settings_manager

    @property
    def extension_runtime(self):
        """Get the extension runtime."""
        return self._context.ext_runtime

    @property
    def extension_generation(self) -> int:
        """Generation used to reject contexts captured before lifecycle replacement."""
        return self._extension_generation

    def _begin_extension_callback(self) -> None:
        """Mark entry into an extension callback."""
        self._extension_callback_depth += 1
        self._extension_callbacks_idle.clear()

    def _end_extension_callback(self) -> None:
        """Mark exit from an extension callback."""
        self._extension_callback_depth = max(0, self._extension_callback_depth - 1)
        if self._extension_callback_depth == 0:
            self._extension_callbacks_idle.set()

    @property
    def resource_diagnostics(self) -> tuple[ResourceDiagnostic, ...]:
        """Return diagnostics produced by the latest resource discovery."""
        snapshot = self._context.resource_snapshot
        return snapshot.diagnostics if snapshot is not None else ()

    def subscribe(self, listener: Handler) -> Unsubscribe:
        """Subscribe to every runtime event and return an unsubscribe callback."""
        return self._context.hooks.subscribe(listener)

    async def steer(self, message: str) -> None:
        """Queue a steering message for the active agent turn."""
        from tau.message.types import UserMessage

        await self._context.engine.steer(UserMessage.from_text(message))

    async def follow_up(self, message: str) -> None:
        """Queue a message to run after the active agent turn finishes."""
        from tau.message.types import UserMessage

        await self._context.engine.follow_up(UserMessage.from_text(message))

    @property
    def extension_shortcuts(self):
        """Get all registered extension keyboard shortcuts."""
        if self._context.ext_runtime is not None:
            return self._context.ext_runtime.get_shortcuts()
        return []

    def set_layout(self, layout: Layout) -> None:
        """Set the TUI layout, making it available to internal services."""
        self._layout = layout

    def set_extension_ui_refresh(self, callback: Callable[[], None]) -> None:
        """Register the interactive-mode extension UI refresh callback."""
        self._extension_ui_refresh = callback

    def notify(self, message: str) -> None:
        """Post a system status note to the active TUI, if attached."""
        if self._layout is None:
            return
        import time

        from tau.message.types import CustomMessage, LinesContent

        msg = CustomMessage(
            custom_type="system",
            timestamp=time.time(),
            contents=[LinesContent(lines=[message, ""])],
        )
        self._layout.add_message(msg)

    # -------------------------------------------------------------------------
    # Core input entry point
    # -------------------------------------------------------------------------

    async def user_input(self, text: str, options: PromptOptions | None = None) -> None:
        """Accept raw user text. ! runs a shell command; / goes to CommandRegistry;
        everything else to the agent.
        """
        match text.strip():
            case "":
                return
            case t if t.startswith("!!"):
                await self.execute_terminal(t[2:].strip(), exclude=True)
            case t if t.startswith("!"):
                await self.execute_terminal(t[1:].strip())
            case t if t.startswith("/skill:"):
                skill_part = t[7:].strip().split(None, 1)
                skill_name = skill_part[0].lower() if skill_part else ""
                skill_args = skill_part[1] if len(skill_part) > 1 else ""
                from tau.skills.registry import skill_registry

                skill = skill_registry.get(skill_name)
                if skill is not None:
                    expanded = (
                        f'<skill name="{skill.name}" location="{skill.file_path}">\n'
                        f"References are relative to {skill.base_dir}.\n\n"
                        f"{skill.content}\n</skill>"
                    )
                    if skill_args:
                        expanded += f"\n\n{skill_args}"
                    await self.invoke(expanded, options)
            case t if t.startswith("/"):
                parts = t[1:].strip().split()
                name, args = parts[0].lower(), parts[1:]
                cmd = ParsedCommand(name=name, args=args, raw=t)
                dispatched = await self.commands.dispatch(cmd)
                if not dispatched:
                    from tau.prompts.registry import prompt_registry

                    expanded = prompt_registry.expand(name, " ".join(args))
                    if expanded is not None:
                        await self.invoke(expanded, options)
                    else:
                        self.notify(f"Unknown command: /{name}")
            case t:
                await self.invoke(t, options)

    async def set_model(self, model_id: str, provider: str | None = None) -> bool:
        """Swap the active model. Only safe to call when the agent is idle.

        Returns ``True`` if the swap succeeded, ``False`` if there is no active
        agent or the model could not be constructed (e.g. unknown id or missing
        credentials for its provider).
        """
        from tau.hooks.tui import ModelSelectEvent
        from tau.inference.api.text.service import TextLLM
        from tau.runtime.dependencies import LLMFactoryContext

        agent = self._context.agent
        if agent is None:
            return False
        old_llm = agent._engine.llm
        old_model = old_llm.model
        try:
            llm_factory = self._config.dependencies.llm
            new_llm = (
                llm_factory(
                    LLMFactoryContext(
                        model_id=model_id,
                        provider=provider,
                        settings=self._context.settings_manager,
                    )
                )
                if llm_factory is not None and self._context.settings_manager is not None
                else TextLLM(model_id=model_id, provider=provider)
            )
        except Exception:
            return False
        if new_llm.model.thinking:
            sm = self._context.settings_manager
            saved_level = sm.get_thinking_level() if sm is not None else None
            # Clamp against the new model's supported levels — a level valid on
            # the previous model (e.g. Max) may not be valid here.
            clamped = new_llm.model.clamp_thinking_level(saved_level)
            new_llm.api.options.thinking_level = clamped or new_llm.model.default_thinking_level
        # An explicit model switch means history may contain provider-specific
        # opaque state (e.g. Gemini's thoughtSignature) minted under a different
        # backend. Replaying it as-is to whichever provider ends up active next
        # risks sending bytes that backend never signed (Google's Cloud Code
        # Assist API rejects malformed thoughtSignature outright). Coarse but
        # safe: once any switch happens, distrust all prior signatures for the
        # rest of the session, rather than trying to prove which turns are safe.
        new_llm.api.options.distrust_thought_signatures = True
        agent._engine.set_llm(new_llm)
        agent._context_window = new_llm.model.input_limit or 128_000
        # The outgoing provider's client (connection pool, kept-alive sockets)
        # is otherwise never released — nothing else holds a reference to
        # old_llm once it's replaced above, and there's no other lifecycle
        # hook that would close it. A no-op if old_llm never actually made a
        # request (its client was never lazily constructed). Best-effort:
        # a close failure shouldn't undo an already-successful model swap.
        try:
            await old_llm.api.aclose()
        except Exception:
            _log.warning("failed to close outgoing model's client", exc_info=True)
        await self._context.hooks.emit(
            ModelSelectEvent(
                model=new_llm.model,
                previous_model=old_model,
                source="set",
            )
        )
        session = self._context.session_manager
        if session is not None:
            session.append_model_change(model_id, new_llm.provider_id)

        sm = self._context.settings_manager
        if sm is not None:
            sm.set_model_ref("text", new_llm.provider_id, model_id)
        return True

    async def execute_terminal(self, cmd: str, exclude: bool = False) -> None:
        """Run a shell command, stream output chunks, persist to session, and emit events."""
        import asyncio
        from asyncio.subprocess import PIPE, STDOUT

        from tau.hooks.runtime import UserTerminalResult
        from tau.hooks.types import (
            TerminalExecutionEvent,
            TerminalOutputEvent,
            UserTerminalEvent,
        )
        from tau.message.types import TerminalExecutionMessage

        exit_code: int | None = None
        cancelled = False
        cwd = str(self._context.session_manager.cwd)

        # Let extensions intercept before the shell runs
        terminal_results = await self._context.hooks.emit(
            UserTerminalEvent(command=cmd, private=exclude, cwd=cwd)
        )
        for r in terminal_results:
            if isinstance(r, UserTerminalResult) and r.handled:
                msg = TerminalExecutionMessage(
                    command=cmd, output=r.output, exit_code=r.exit_code, exclude=exclude
                )
                sm = self._context.session_manager
                if sm is not None:
                    sm.append_message(msg)
                await self._context.hooks.emit(TerminalExecutionEvent(message=msg, streaming=False))
                return

        msg = TerminalExecutionMessage(command=cmd, output="", exclude=exclude)

        await self._context.hooks.emit(TerminalExecutionEvent(message=msg, streaming=True))

        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd.strip(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=PIPE,
                stderr=STDOUT,
                cwd=cwd,
            )
            if proc.stdout is not None:
                async for line in proc.stdout:
                    msg.output += line.decode(errors="replace")
                    await self._context.hooks.emit(TerminalOutputEvent(message=msg))
            await proc.wait()
            exit_code = proc.returncode
        except Exception as exc:
            msg.output += f"error: {exc}"
            cancelled = True
        finally:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        msg.output = msg.output.rstrip()
        msg.exit_code = exit_code
        msg.cancelled = cancelled

        sm = self._context.session_manager
        if sm is not None:
            sm.append_message(msg)

        await self._context.hooks.emit(TerminalExecutionEvent(message=msg, streaming=False))

    async def invoke(
        self,
        text: str,
        options: PromptOptions | None = None,
        *,
        display: bool = False,
    ) -> None:
        """Forward a plain prompt to the current session.

        Set ``display`` when the caller did not originate from the interactive
        input handler and the prompt should appear in the active TUI transcript.
        """
        if self._context.agent is None:
            raise RuntimeError("No active session available.")
        if display and self._layout is not None:
            from tau.message.types import UserMessage

            self._layout.add_message(UserMessage.from_text(text))
            self._layout._tui.request_render()
        results = await self._context.hooks.emit(InputEvent(text=text))
        for r in results:
            if isinstance(r, InputEventResult) and r.action == "transform" and r.text is not None:
                text = r.text
                break
        await self._context.agent.invoke(text, options)

    async def reload_extensions(self):
        """Reload now when safe, otherwise queue one reload for the next safe boundary."""
        from tau.extensions.api import LoadExtensionsResult

        if self._stopped:
            return LoadExtensionsResult()
        agent = self._context.agent
        if self._extension_callback_depth > 0 or (agent is not None and not agent.is_idle()):
            self._reload_pending = True
            self._ensure_deferred_reload()
            return LoadExtensionsResult()
        async with self._reload_lock:
            return await self._reload_extensions_now()

    def _ensure_deferred_reload(self) -> None:
        """Start the single task that drains queued reload requests."""
        if self._reload_task is None or self._reload_task.done():
            self._reload_task = asyncio.create_task(self._drain_deferred_reloads())

    async def _drain_deferred_reloads(self) -> None:
        """Run coalesced extension reloads only after callbacks and agent work settle."""
        try:
            while self._reload_pending and not self._stopped:
                self._reload_pending = False
                await self._extension_callbacks_idle.wait()
                agent = self._context.agent
                if agent is not None and not agent.is_idle():
                    await agent.wait_for_idle()
                if self._extension_callback_depth > 0:
                    self._reload_pending = True
                    continue
                async with self._reload_lock:
                    await self._reload_extensions_now()
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("deferred extension reload failed")
        finally:
            self._reload_task = None

    async def _reload_extensions_now(self):
        """Re-discover and reload extensions, skills, prompts, and settings.

        Applies all changes to the live engine and rebuilds the system prompt
        immediately — no new session required.
        """
        from tau.agent.prompt.builder import build_prompt
        from tau.agent.prompt.types import PromptOptions
        from tau.extensions.api import LoadExtensionsResult, _RuntimeRef
        from tau.extensions.runtime import ExtensionRuntime
        from tau.resources.types import ResourceContext
        from tau.skills.registry import skill_registry

        sm = self._context.settings_manager
        if sm is None:
            return LoadExtensionsResult()

        # ── Settings ─────────────────────────────────────────────────────────
        # Skip reload when /settings is open (batch mode): the settings panel
        # holds in-memory changes that haven't been written to disk yet.
        # Reloading from disk here would overwrite those changes and clear
        # modified_fields, causing save_batch() at close to write nothing.
        if not sm.is_batching():
            await sm.reload()

        cwd = self._context.session_manager.cwd

        resource_loader = self._context.resource_loader
        if resource_loader is None:
            return LoadExtensionsResult()
        resource_context = ResourceContext(
            cwd=cwd,
            settings=sm,
            hooks=self._context.hooks,
            load_context_files=not self._config.disable_context_files
            and self._context.project_trusted,
        )
        resources = await resource_loader.discover(resource_context)
        resource_loader.apply_registries(resources, context=resource_context)
        self._context.resource_snapshot = resources

        old = self._context.ext_runtime
        self._extension_generation += 1
        if old is not None:
            for ext in old._extensions:
                await self._emit_to_extension(ext, "extension_unload")
            old.unsubscribe()

        runtime_ref = old.runtime_ref if old is not None else _RuntimeRef()
        runtime_ref.services.clear()
        runtime_ref.service_owners.clear()

        loader = resource_loader.create_extension_loader(
            resources,
            context=resource_context,
            llm=self._context.llm,
            runtime_ref=runtime_ref,
        )
        load_result = await loader.load()
        if self._config.extension_factories:
            from tau.extensions.loader import load_inline_extensions

            inline_result = await load_inline_extensions(
                self._config.extension_factories,
                llm=self._context.llm,
                settings=sm,
                cwd=cwd,
                runtime_ref=runtime_ref,
            )
            load_result.extensions.extend(inline_result.extensions)
            load_result.errors.extend(inline_result.errors)
        new_ext = ExtensionRuntime(load_result, self._context.hooks, runtime_ref)
        new_ext.runtime_ref.runtime = self
        self._context.ext_runtime = new_ext

        self.commands.replace_source("extension", new_ext.get_commands())

        # ── Sync tools via registry then push to engine ───────────────────────
        engine = self._context.engine
        agent = self._context.agent
        if engine is not None:
            registry = self._context.tool_registry
            registry.replace_source(
                "extension",
                [tool for tool in new_ext.get_tools() if self._tool_enabled(tool.name)],
            )
            registry.sync_to_engine(engine, layout=getattr(self, "_layout", None))

            if agent is not None:
                extra_appends = new_ext.get_prompt_appends()
                skills = skill_registry.list()
                agent._system_prompt = (
                    self._config.system_prompt
                    or resources.system_prompt
                    or build_prompt(
                        PromptOptions(
                            cwd=cwd,
                            tools=registry.list(),
                            extra_appends=extra_appends,
                            skills=skills,
                            disable_context_files=self._config.disable_context_files,
                            project_trusted=self._context.project_trusted,
                            context_files=resources.context_files,
                        )
                    )
                )

        for ext in new_ext.get_extensions():
            await self._emit_to_extension(ext, "extension_reloaded")
        if self._extension_ui_refresh is not None:
            self._extension_ui_refresh()

        return load_result

    async def reload_extension(self, ext_path: str):
        """Reload one extension now when safe, otherwise queue a full reload."""
        from tau.extensions.api import LoadExtensionsResult

        if self._stopped:
            return LoadExtensionsResult()
        agent = self._context.agent
        if self._extension_callback_depth > 0 or (agent is not None and not agent.is_idle()):
            self._reload_pending = True
            self._ensure_deferred_reload()
            return LoadExtensionsResult()
        async with self._reload_lock:
            return await self._reload_extension_now(ext_path)

    async def _reload_extension_now(self, ext_path: str):
        """Reload a single extension by its loaded module path, applying live.

        Re-reads settings, re-runs only this extension's ``register`` with fresh
        config, and swaps its tools/commands/prompt in place — other extensions
        keep their existing state and are *not* re-run (so their resources and
        side effects are untouched). Falls back to a full reload if the target
        can't be resolved.
        """
        from pathlib import Path

        from tau.agent.prompt.builder import build_prompt
        from tau.agent.prompt.types import PromptOptions
        from tau.extensions.api import LoadExtensionsResult
        from tau.extensions.loader import ExtensionLoader
        from tau.extensions.runtime import ExtensionRuntime
        from tau.settings.paths import get_extensions_dir
        from tau.skills.registry import skill_registry

        sm = self._context.settings_manager
        if sm is None:
            return LoadExtensionsResult()

        if not sm.is_batching():
            await sm.reload()

        old = self._context.ext_runtime
        if old is None:
            return await self._reload_extensions_now()
        target = next((e for e in old._extensions if e.path == ext_path), None)
        if target is None:
            # Unknown target — fall back to the all-extensions reload.
            return await self._reload_extensions_now()
        if target.source == "inline":
            return await self._reload_extensions_now()

        cwd = self._context.session_manager.cwd
        entries = sm.get_all_extension_entries()
        entry_configs = {Path(e.path).stem: (e.settings or {}) for e in entries if e.enabled}
        p = Path(ext_path)
        stem = p.parent.name if p.name == "__init__.py" else p.stem
        config = entry_configs.get(stem, {})

        runtime_ref = old.runtime_ref
        stale_services = [
            name for name, owner in runtime_ref.service_owners.items() if owner == target.path
        ]
        for name in stale_services:
            runtime_ref.services.pop(name, None)
            runtime_ref.service_owners.pop(name, None)
        loader = ExtensionLoader(
            project_dir=get_extensions_dir(cwd),
            global_dir=get_extensions_dir(),
            llm=self._context.llm,
            settings=sm,
            cwd=cwd,
            runtime_ref=runtime_ref,
        )
        # Populate the per-subdir caches (deps + manifest settings schema) for
        # this extension so _load_one re-attaches its auto-generated panel.
        loader._subdir_entries(p.parent)
        new_ext, errs = await loader._load_one(p, config, source=target.source)
        if new_ext is None:
            # Keep the old extension on failure; surface load errors.
            return LoadExtensionsResult(extensions=old._extensions, errors=errs)

        # Let the outgoing extension release any resources it holds (subprocesses,
        # background tasks, sockets) before it is replaced — reload does not do
        # this automatically, so stateful extensions must handle `extension_unload`.
        self._extension_generation += 1
        await self._emit_to_extension(target, "extension_unload")

        new_list = [new_ext if e is target else e for e in old._extensions]
        old.unsubscribe()
        new_runtime = ExtensionRuntime(
            LoadExtensionsResult(extensions=new_list, errors=errs),
            self._context.hooks,
            runtime_ref,
        )
        new_runtime.runtime_ref.runtime = self
        self._context.ext_runtime = new_runtime

        self.commands.replace_source("extension", new_runtime.get_commands())

        # ── Tools + prompt ────────────────────────────────────────────────────
        engine = self._context.engine
        agent = self._context.agent
        if engine is not None:
            registry = self._context.tool_registry
            registry.replace_source(
                "extension",
                [tool for tool in new_runtime.get_tools() if self._tool_enabled(tool.name)],
            )
            registry.sync_to_engine(engine, layout=getattr(self, "_layout", None))

            if agent is not None:
                snapshot = self._context.resource_snapshot
                agent._system_prompt = (
                    self._config.system_prompt
                    or (snapshot.system_prompt if snapshot is not None else None)
                    or build_prompt(
                        PromptOptions(
                            cwd=cwd,
                            tools=registry.list(),
                            extra_appends=new_runtime.get_prompt_appends(),
                            skills=skill_registry.list(),
                            disable_context_files=self._config.disable_context_files,
                            project_trusted=self._context.project_trusted,
                            context_files=(
                                snapshot.context_files if snapshot is not None else None
                            ),
                        )
                    )
                )

        # Give the freshly-loaded extension a chance to re-establish runtime state
        # (e.g. warm up language servers) now that the runtime is already wired —
        # `runtime_ready` only fires once at startup, not on reload.
        await self._emit_to_extension(new_ext, "extension_reloaded")
        if self._extension_ui_refresh is not None:
            self._extension_ui_refresh()

        return LoadExtensionsResult(extensions=new_list, errors=errs)

    def _tool_enabled(self, name: str) -> bool:
        """Return whether a tool name passes runtime allow/exclude filters."""
        allowlist = self._config.tool_allowlist
        return (allowlist is None or name in allowlist) and name not in self._config.exclude_tools

    async def _emit_to_extension(self, ext, event_type: str) -> None:
        """Dispatch a lifecycle event directly to a single extension's handlers.

        Used for reload-only events (``extension_unload`` / ``extension_reloaded``)
        that must reach exactly one extension rather than every handler on the bus.
        Handler exceptions are swallowed so one bad handler can't block the reload.

        Bounded by ``_SHUTDOWN_HOOK_TIMEOUT``: this runs under ``self._reload_lock``,
        so a handler with no timeout of its own (an unbounded network call, a
        deadlock) would otherwise hang forever and wedge every future
        reload/toggle for the rest of the session — not just fail the current one.
        """
        import inspect
        from types import SimpleNamespace

        from tau.extensions.context import ExtensionContext

        handlers = ext.handlers.get(event_type, [])
        if not handlers:
            return
        ctx = ExtensionContext.from_runtime(self)
        event = SimpleNamespace(type=event_type)
        for handler in handlers:
            self._begin_extension_callback()
            try:
                result = handler(event, ctx)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, _SHUTDOWN_HOOK_TIMEOUT)
            except TimeoutError:
                _log.warning(
                    "extension %s handler for %r timed out after %.0fs; skipping",
                    ext.path,
                    event_type,
                    _SHUTDOWN_HOOK_TIMEOUT,
                )
            except Exception:
                # Don't let one failed handler abort the reload, but never fail
                # silently — a botched dispose (e.g. servers not reaped) must be
                # visible rather than leaking resources unnoticed.
                _log.exception("extension %s handler for %r raised", ext.path, event_type)
            finally:
                self._end_extension_callback()

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    async def new_session(self, *, with_session=None) -> None:
        """Shut down the current session and start a fresh one."""
        await self._emit_session_shutdown(SessionShutdownReason.New)
        self._extension_generation += 1
        # ``resume`` is a startup instruction, not persistent runtime state.
        # Leaving it enabled makes /new call continue_recent() and reopen the
        # previous conversation instead of creating an empty session.
        self._config = self._config.model_copy(update={"session_file": None, "resume": False})
        self._context = await RuntimeContext.create(
            self._config,
            settings_manager=self.settings_manager,
            hooks=self.hooks,
            ext_runtime=self.extension_runtime,
        )
        self._reinit_after_context_create()
        await self._run_with_session(with_session)
        await self._emit_session_start(SessionStartReason.New)

    async def resume_session(self, session_file: Path, *, with_session=None) -> None:
        """Shut down the current session and resume an existing one from a file."""
        session_file = Path(session_file).resolve()

        before_results = await self._context.hooks.emit(
            SessionBeforeSwitchEvent(
                reason=SessionBeforeSwitchReason.Resume, target_session_file=str(session_file)
            )
        )
        for r in before_results:
            if isinstance(r, SessionBeforeSwitchResult) and r.cancel:
                return

        await self._emit_session_shutdown(SessionShutdownReason.Resume)
        self._extension_generation += 1
        self._config = self._config.model_copy(update={"session_file": session_file})
        self._context = await RuntimeContext.create(
            self._config,
            settings_manager=self.settings_manager,
            hooks=self.hooks,
            ext_runtime=self.extension_runtime,
        )
        self._reinit_after_context_create()
        await self._run_with_session(with_session)
        await self._emit_session_start(SessionStartReason.Resume)

    async def navigate_tree(
        self,
        target_id: str,
        *,
        summarize: bool = False,
        custom_instructions: str | None = None,
        replace_instructions: bool = False,
        label: str | None = None,
    ) -> bool:
        """Navigate the session tree to target_id, optionally generating a branch summary.

        Returns False if cancelled by an extension handler, True otherwise.
        """
        sm = self._context.session_manager
        if target_id not in sm.by_id:
            raise KeyError(f"Entry '{target_id}' not found in session.")

        old_leaf_id = sm.get_leaf_id()
        if target_id == old_leaf_id:
            return True  # already there

        # Collect entries between the old leaf and the common ancestor
        from tau.session.branch_summarization import collect_entries_for_branch_summary

        collect_result = collect_entries_for_branch_summary(sm, old_leaf_id, target_id)
        entries_to_summarize = collect_result.entries
        common_ancestor_id = collect_result.common_ancestor_id

        # Build preparation for the before_tree hook
        preparation = TreePreparation(
            target_id=target_id,
            old_leaf_id=old_leaf_id,
            common_ancestor_id=common_ancestor_id,
            entries_to_summarize=entries_to_summarize,
            custom_instructions=custom_instructions,
            replace_instructions=replace_instructions,
            label=label,
        )
        operation_active = summarize and bool(entries_to_summarize)
        agent = self._context.agent
        previous_phase = agent._phase
        phase_changed = operation_active
        if operation_active:
            agent._phase = AgentPhase.BRANCH_SUMMARY

        try:
            results = await self._context.hooks.emit(
                SessionBeforeTreeEvent(preparation=preparation)
            )
            custom_instructions = preparation.custom_instructions
            replace_instructions = preparation.replace_instructions
            label = preparation.label
            summary_text: str | None = None
            summary_details: dict | None = None
            from_extension = False
            for result in results:
                if not isinstance(result, SessionBeforeTreeResult):
                    continue
                if result.cancel:
                    if operation_active:
                        await self._context.hooks.emit(
                            BranchSummaryCancelledEvent(
                                old_leaf_id=old_leaf_id,
                                target_id=target_id,
                                reason="cancelled by extension",
                            )
                        )
                    return False
                if result.custom_instructions is not None:
                    custom_instructions = result.custom_instructions
                if result.replace_instructions is not None:
                    replace_instructions = result.replace_instructions
                if result.label is not None:
                    label = result.label
                if result.summary is not None and summary_text is None:
                    summary_text = result.summary
                    summary_details = result.summary_details
                    from_extension = True

            summary_active = operation_active or summary_text is not None
            if summary_active and not phase_changed:
                agent._phase = AgentPhase.BRANCH_SUMMARY
                phase_changed = True
            if summary_active:
                await self._context.hooks.emit(
                    BranchSummaryStartEvent(
                        old_leaf_id=old_leaf_id,
                        target_id=target_id,
                        from_extension=from_extension,
                    )
                )

            if operation_active and summary_text is None:
                sm_settings = self._context.settings_manager
                reserve_tokens = (
                    sm_settings.get_branch_summary_reserve_tokens()
                    if sm_settings is not None
                    else 16_384
                )
                from tau.session.branch_summarization import generate_branch_summary

                llm = self._context.llm
                result = await generate_branch_summary(
                    entries_to_summarize,
                    llm,
                    context_window=llm.model.input_limit or 128_000,
                    reserve_tokens=reserve_tokens,
                    custom_instructions=custom_instructions,
                    replace_instructions=replace_instructions,
                )
                if result.error:
                    _log.warning("Branch summary failed: %s", result.error)
                    self.notify(f"Branch summary failed: {result.error}")
                    await self._context.hooks.emit(
                        BranchSummaryFailureEvent(
                            old_leaf_id=old_leaf_id,
                            target_id=target_id,
                            error=result.error,
                        )
                    )
                elif result.aborted:
                    _log.info("Branch summary cancelled; navigating without a summary")
                    self.notify("Branch summary cancelled; switched branches without a summary.")
                    await self._context.hooks.emit(
                        BranchSummaryCancelledEvent(
                            old_leaf_id=old_leaf_id,
                            target_id=target_id,
                            reason="provider request aborted",
                        )
                    )
                elif result.summary:
                    summary_text = result.summary
                    summary_details = {
                        "read_files": result.read_files,
                        "modified_files": result.modified_files,
                    }

            sm.branch(target_id)
            summary_entry_id = ""
            if summary_text is not None:
                summary_entry_id = sm.append_branch_summary(
                    from_id=old_leaf_id or "",
                    summary=summary_text,
                    label=label,
                    details=summary_details,
                    from_hook=from_extension,
                )
                await self._context.hooks.emit(
                    BranchSummaryEndEvent(
                        old_leaf_id=old_leaf_id,
                        target_id=target_id,
                        summary_entry_id=summary_entry_id,
                        summary_length=len(summary_text),
                        from_extension=from_extension,
                    )
                )
            await self._context.hooks.emit(
                SessionTreeEvent(
                    new_leaf_id=target_id,
                    old_leaf_id=old_leaf_id,
                    from_extension=from_extension,
                )
            )
            await self._emit_session_start(SessionStartReason.Fork)
            return True
        finally:
            if phase_changed:
                agent._phase = previous_phase

    async def fork_session(
        self,
        from_entry_id: str,
        *,
        position: str = "at",
        with_session=None,
    ) -> None:
        """Branch the session tree at the given entry and start a new leaf."""
        sm = self._context.session_manager
        if from_entry_id not in sm.by_id:
            raise KeyError(f"Entry '{from_entry_id}' not found in session.")

        before_results = await self._context.hooks.emit(
            SessionBeforeForkEvent(entry_id=from_entry_id, position=position)  # type: ignore[arg-type]
        )
        for r in before_results:
            if isinstance(r, SessionBeforeForkResult) and r.cancel:
                return

        sm.branch(from_entry_id)
        await self._run_with_session(with_session)
        await self._emit_session_start(SessionStartReason.Fork)

    async def clone_session(self) -> None:
        """Duplicate the current branch into a new session file and switch to it."""
        sm = self._context.session_manager
        leaf_id = sm.get_leaf_id()
        if leaf_id is None:
            raise ValueError("No active leaf to clone from.")

        await self._emit_session_shutdown(SessionShutdownReason.Clone)
        self._extension_generation += 1
        sm.create_branched_session(leaf_id)
        self._reinit_after_context_create()
        await self._emit_session_start(SessionStartReason.Clone)

    def _reinit_after_context_create(self) -> None:
        if self._context.agent is not None:
            self._context.agent._runtime = self
        # Keep the runtime ref pointing at this Runtime instance
        if self._context.ext_runtime is not None:
            self._context.ext_runtime.runtime_ref.runtime = self

    async def _run_with_session(self, with_session) -> None:
        """Call the with_session(ctx) callback if provided, with a fresh context."""
        if with_session is None:
            return
        import inspect

        from tau.extensions.context import ExtensionContext

        ctx = ExtensionContext.from_runtime(self)
        try:
            result = with_session(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _log.exception("with_session callback raised")

    # -------------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------------

    def shutdown(self) -> None:
        pass

    async def ashutdown(self) -> None:
        """Tear down the runtime once the mode-specific loop has exited.

        Emits `runtime_stop` (symmetric to the `runtime_ready` emitted in
        `create`) so extensions can run terminal cleanup that must happen on quit
        regardless of mode. Idempotent — guarded so a double call is a no-op.
        """
        if self._stopped:
            return
        self._stopped = True
        self._extension_generation += 1
        if self._reload_task is not None and not self._reload_task.done():
            self._reload_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._reload_task, _SHUTDOWN_TASK_TIMEOUT)
        self._reload_pending = False
        if self.version_check_task is not None and not self.version_check_task.done():
            self.version_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self.version_check_task, _SHUTDOWN_TASK_TIMEOUT)
        telemetry_task = getattr(self, "telemetry_task", None)
        if telemetry_task is not None and not telemetry_task.done():
            telemetry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(telemetry_task, _SHUTDOWN_TASK_TIMEOUT)
        await self._context.hooks.emit(RuntimeStopEvent(), timeout=_SHUTDOWN_HOOK_TIMEOUT)
        if self._context.ext_runtime is not None:
            self._context.ext_runtime.unsubscribe()

    # -------------------------------------------------------------------------
    # Event helpers
    # -------------------------------------------------------------------------

    async def _emit_session_start(self, reason: SessionStartReason) -> None:
        await self._context.hooks.emit(SessionStartEvent(reason=reason))

    async def _emit_session_shutdown(self, reason: SessionShutdownReason) -> None:
        await self._context.hooks.emit(SessionShutdownEvent(reason=reason))
