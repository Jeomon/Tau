from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from tau.agent.service import Agent
from tau.agent.types import PromptOptions
from tau.commands.registry import CommandRegistry
from tau.commands.types import ParsedCommand
from tau.hooks.runtime import InputEvent, InputEventResult, RuntimeReadyEvent, RuntimeStopEvent
from tau.hooks.session import (
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
from tau.runtime.types import RuntimeConfig, RuntimeContext

if TYPE_CHECKING:
    from tau.modes.interactive.components.layout import Layout

_log = logging.getLogger(__name__)


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
        self._stopped: bool = False
        self.version_check_task: asyncio.Task[str | None] | None = None
        if context.agent is not None:
            context.agent._runtime = self
        # Bind runtime ref so (event, ctx) handlers resolve live state
        if context.ext_runtime is not None:
            context.ext_runtime.runtime_ref.runtime = self
        # Register extension commands into the command registry
        if context.ext_runtime is not None:
            for cmd in context.ext_runtime.get_commands():
                self.commands.register(cmd)

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(cls, config: RuntimeConfig) -> Runtime:
        """Create a fully initialised Runtime from config and fire the session_start event."""
        context = await RuntimeContext.create(config=config)
        runtime = cls(context=context, config=config)
        runtime._start_version_check()
        await runtime._emit_session_start(SessionStartReason.Startup)
        # Runtime is now fully wired (engine, agent, tools, extensions) and the
        # session has started, but no mode-specific loop (TUI/print/rpc) has begun.
        # Extensions can hook `runtime_ready` to start background work from here.
        await context.hooks.emit(RuntimeReadyEvent())
        return runtime

    def _start_version_check(self) -> None:
        from tau.settings.paths import get_app_version
        from tau.utils.version_check import check_for_new_version

        self.version_check_task = asyncio.ensure_future(check_for_new_version(get_app_version()))

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
    def extension_shortcuts(self):
        """Get all registered extension keyboard shortcuts."""
        if self._context.ext_runtime is not None:
            return self._context.ext_runtime.get_shortcuts()
        return []

    def set_layout(self, layout: Layout) -> None:
        """Set the TUI layout, making it available to internal services."""
        self._layout = layout

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

        agent = self._context.agent
        if agent is None:
            return False
        old_model = agent._engine.llm.model
        try:
            new_llm = TextLLM(model_id=model_id, provider=provider)
        except Exception:
            return False
        if new_llm.model.thinking:
            new_llm.api.options.thinking_level = new_llm.model.thinking_level
        agent._engine.set_llm(new_llm)
        agent._context_window = new_llm.model.input_limit or 128_000
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

    async def invoke(self, text: str, options: PromptOptions | None = None) -> None:
        """Forward a plain prompt to the current session."""
        if self._context.agent is None:
            raise RuntimeError("No active session available.")
        results = await self._context.hooks.emit(InputEvent(text=text))
        for r in results:
            if isinstance(r, InputEventResult) and r.action == "transform" and r.text is not None:
                text = r.text
                break
        await self._context.agent.invoke(text, options)

    async def reload_extensions(self):
        """Re-discover and reload extensions, skills, prompts, and settings.

        Applies all changes to the live engine and rebuilds the system prompt
        immediately — no new session required.
        """
        from pathlib import Path

        from tau.agent.prompt.builder import build_prompt
        from tau.agent.prompt.types import PromptOptions
        from tau.extensions.api import LoadExtensionsResult, _RuntimeRef
        from tau.extensions.loader import ExtensionLoader
        from tau.extensions.runtime import ExtensionRuntime
        from tau.prompts.registry import prompt_registry
        from tau.settings.paths import get_extensions_dir
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

        # ── Resource discovery hook ──────────────────────────────────────────
        from tau.hooks.runtime import ResourcesDiscoverResult
        from tau.hooks.types import ResourcesDiscoverEvent

        discover_results = await self._context.hooks.emit(ResourcesDiscoverEvent(cwd=str(cwd)))
        extra_skill_paths: list[str] = []
        extra_prompt_paths: list[str] = []
        extra_theme_paths: list[str] = []
        for r in discover_results:
            if isinstance(r, ResourcesDiscoverResult):
                extra_skill_paths.extend(r.skill_paths)
                extra_prompt_paths.extend(r.prompt_paths)
                extra_theme_paths.extend(r.theme_paths)

        # ── Skills ───────────────────────────────────────────────────────────
        skill_registry.reload(cwd=cwd, extra_paths=extra_skill_paths or None)  # type: ignore[arg-type]

        # ── Prompts ──────────────────────────────────────────────────────────
        prompt_registry.reload(cwd=cwd, extra_paths=extra_prompt_paths or None)  # type: ignore[arg-type]

        # extra_theme_paths: load each directory into the theme registry
        if extra_theme_paths:
            from tau.themes.registry import theme_registry

            for tp in extra_theme_paths:
                try:
                    from pathlib import Path as _Path

                    for _err in theme_registry.load_external(cwd=_Path(tp)):
                        _log.warning("theme load error in %r: %s: %s", tp, _err.path, _err.error)
                except Exception as _e:
                    _log.warning("resources_discover: failed to load theme path %r: %s", tp, _e)

        old = self._context.ext_runtime
        if old is not None:
            for ext in old._extensions:
                await self._emit_to_extension(ext, "extension_unload")
                # Drop the outgoing extension's commands; the new set is
                # re-registered below. Without this, a now-disabled extension's
                # commands would linger in the registry until restart.
                for name in ext.commands:
                    self.commands.unregister(name)
            old.unsubscribe()

        entries = sm.get_all_extension_entries()
        disabled_stems = {Path(e.path).stem for e in entries if not e.enabled}
        entry_configs = {Path(e.path).stem: (e.settings or {}) for e in entries if e.enabled}
        extra_entries = [e for e in entries if e.enabled]
        runtime_ref = old.runtime_ref if old is not None else _RuntimeRef()

        loader = ExtensionLoader(
            project_dir=get_extensions_dir(cwd),
            global_dir=get_extensions_dir(),
            extra_entries=extra_entries,
            disabled_stems=disabled_stems,
            entry_configs=entry_configs,
            llm=self._context.llm,
            settings=sm,
            cwd=cwd,
            runtime_ref=runtime_ref,
        )
        load_result = await loader.load()
        new_ext = ExtensionRuntime(load_result, self._context.hooks, runtime_ref)
        new_ext.runtime_ref.runtime = self
        self._context.ext_runtime = new_ext

        for cmd in new_ext.get_commands():
            self.commands.register(cmd)

        # ── Sync tools via registry then push to engine ───────────────────────
        engine = self._context.engine
        agent = self._context.agent
        if engine is not None:
            registry = self._context.tool_registry
            registry.replace_source("extension", new_ext.get_tools())
            registry.sync_to_engine(engine, layout=getattr(self, "_layout", None))

            if agent is not None:
                extra_appends = new_ext.get_prompt_appends()
                skills = skill_registry.list()
                agent._system_prompt = build_prompt(
                    PromptOptions(
                        cwd=cwd,
                        tools=registry.list(),
                        extra_appends=extra_appends,
                        skills=skills,
                        disable_context_files=self._config.disable_context_files,
                        project_trusted=self._config.project_trusted,
                    )
                )

        for ext in new_ext._extensions:
            await self._emit_to_extension(ext, "extension_reloaded")

        return load_result

    async def reload_extension(self, ext_path: str):
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
            return await self.reload_extensions()
        target = next((e for e in old._extensions if e.path == ext_path), None)
        if target is None:
            # Unknown target — fall back to the all-extensions reload.
            return await self.reload_extensions()

        cwd = self._context.session_manager.cwd
        entries = sm.get_all_extension_entries()
        entry_configs = {Path(e.path).stem: (e.settings or {}) for e in entries if e.enabled}
        p = Path(ext_path)
        stem = p.parent.name if p.name == "__init__.py" else p.stem
        config = entry_configs.get(stem, {})

        runtime_ref = old.runtime_ref
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

        # ── Commands: drop the target's old commands, register its new set ────
        for name in target.commands:
            self.commands.unregister(name)
        for cmd in new_ext.commands.values():
            self.commands.register(cmd)

        # ── Tools + prompt ────────────────────────────────────────────────────
        engine = self._context.engine
        agent = self._context.agent
        if engine is not None:
            registry = self._context.tool_registry
            registry.replace_source("extension", new_runtime.get_tools())
            registry.sync_to_engine(engine, layout=getattr(self, "_layout", None))

            if agent is not None:
                agent._system_prompt = build_prompt(
                    PromptOptions(
                        cwd=cwd,
                        tools=registry.list(),
                        extra_appends=new_runtime.get_prompt_appends(),
                        skills=skill_registry.list(),
                        disable_context_files=self._config.disable_context_files,
                        project_trusted=self._config.project_trusted,
                    )
                )

        # Give the freshly-loaded extension a chance to re-establish runtime state
        # (e.g. warm up language servers) now that the runtime is already wired —
        # `runtime_ready` only fires once at startup, not on reload.
        await self._emit_to_extension(new_ext, "extension_reloaded")

        return LoadExtensionsResult(extensions=new_list, errors=errs)

    async def _emit_to_extension(self, ext, event_type: str) -> None:
        """Dispatch a lifecycle event directly to a single extension's handlers.

        Used for reload-only events (``extension_unload`` / ``extension_reloaded``)
        that must reach exactly one extension rather than every handler on the bus.
        Handler exceptions are swallowed so one bad handler can't block the reload.
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
            try:
                result = handler(event, ctx)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Don't let one failed handler abort the reload, but never fail
                # silently — a botched dispose (e.g. servers not reaped) must be
                # visible rather than leaking resources unnoticed.
                _log.exception("extension %s handler for %r raised", ext.path, event_type)

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    async def new_session(self, *, with_session=None) -> None:
        """Shut down the current session and start a fresh one."""
        await self._emit_session_shutdown(SessionShutdownReason.New)
        self._config = self._config.model_copy(update={"session_file": None})
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
            custom_instructions=custom_instructions,
            replace_instructions=replace_instructions,
            label=label,
        )

        # Let extensions inspect / mutate / cancel
        results = await self._context.hooks.emit(SessionBeforeTreeEvent(preparation=preparation))
        for r in results:
            if isinstance(r, SessionBeforeTreeResult):
                if r.cancel:
                    return False
                if r.custom_instructions is not None:
                    custom_instructions = r.custom_instructions
                if r.replace_instructions is not None:
                    replace_instructions = r.replace_instructions
                if r.label is not None:
                    label = r.label

        # Generate branch summary if requested
        if summarize and entries_to_summarize:
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
                reserve_tokens=reserve_tokens,
                custom_instructions=custom_instructions,
                replace_instructions=replace_instructions,
            )
            if result.summary and not result.aborted:
                sm.append_branch_summary(
                    from_id=old_leaf_id or "",
                    summary=result.summary,
                    label=label,
                    details={
                        "read_files": result.read_files,
                        "modified_files": result.modified_files,
                    },
                )

        sm.branch(target_id)
        await self._context.hooks.emit(
            SessionTreeEvent(new_leaf_id=target_id, old_leaf_id=old_leaf_id)
        )
        await self._emit_session_start(SessionStartReason.Fork)
        return True

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
        if self.version_check_task is not None and not self.version_check_task.done():
            self.version_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.version_check_task
        await self._context.hooks.emit(RuntimeStopEvent())

    # -------------------------------------------------------------------------
    # Event helpers
    # -------------------------------------------------------------------------

    async def _emit_session_start(self, reason: SessionStartReason) -> None:
        await self._context.hooks.emit(SessionStartEvent(reason=reason))

    async def _emit_session_shutdown(self, reason: SessionShutdownReason) -> None:
        await self._context.hooks.emit(SessionShutdownEvent(reason=reason))
