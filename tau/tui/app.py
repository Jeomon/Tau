from __future__ import annotations

import asyncio
from typing import Callable, TYPE_CHECKING

from tau.tui.input import KeyEvent, InputEvent
from tau.tui.components.layout import Layout
from tau.tui.keybindings import KeyMap, configure_keybindings
from tau.tui.theme import LayoutTheme
from tau.tui.tui import TUI
from tau.tui.commands.context import CommandContext
from tau.tui.agent_hooks import AgentHookHandler
from tau.tui.input_handler import InputHandler
from tau.extensions import ExtensionContext

if TYPE_CHECKING:
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig


class App:
    """
    Wires the TUI layout to the agent runtime.

    Delegates to focused collaborators:
      - AgentHookHandler  — subscribes to agent events, drives spinner/messages
      - InputHandler      — submit, paste, clipboard, steer, history
      - tau.tui.commands.* — slash command logic, each receiving a CommandContext

    Usage::

        config = RuntimeConfig(cwd=Path.cwd(), model_id="claude-sonnet-4-6")
        app = await App.create(config)
        await app.run()
    """

    def __init__(self, runtime: Runtime, tui: TUI, layout: Layout) -> None:
        self._runtime = runtime
        self._tui = tui
        self._layout = layout
        self._input = InputHandler(runtime, layout, tui)
        self._hooks = AgentHookHandler(
            runtime, layout, tui,
            on_palette_refresh=self.refresh_palette,
            on_turn_content=self._input.mark_turn_content,
        )
        self._unsubs: list[Callable[[], None]] = []
        self._last_ctrl_c: float = 0.0
        self._last_escape: float = 0.0

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        runtime: Runtime,
        theme: LayoutTheme | str | None = None,
        keybindings: KeyMap | None = None,
    ) -> App:
        """Build the TUI around an already-constructed Runtime."""
        from tau.themes.registry import theme_registry, DEFAULT_THEME

        cwd = runtime.session_manager.cwd if runtime.session_manager is not None else None
        theme_registry.load_external(cwd=cwd)

        from tau.prompts.registry import prompt_registry
        prompt_registry.load_external(cwd=cwd)

        resolved_theme: LayoutTheme | None
        theme_name = DEFAULT_THEME
        if isinstance(theme, str):
            theme_name = theme
            resolved_theme = theme_registry.get(theme_name)
        elif theme is None:
            sm = runtime.settings_manager
            theme_name = (sm.get_theme() if sm is not None else None) or DEFAULT_THEME
            try:
                resolved_theme = theme_registry.get(theme_name)
            except ValueError:
                # Configured theme is gone (e.g. an uninstalled theme package)
                # or the default builtin is missing — fall back to a theme that
                # is guaranteed to load instead of crashing on startup.
                theme_name = DEFAULT_THEME
                resolved_theme = theme_registry.get_default()
        else:
            resolved_theme = theme

        sm = runtime.settings_manager
        picker_max_visible = 8
        autocomplete_max_visible = 5
        if sm is not None:
            resolved_theme.message.show_thinking   = sm.get_show_thinking()
            resolved_theme.message.show_tool_calls = sm.get_show_tool_calls()
            resolved_theme.message.show_images     = sm.get_show_images()
            picker_max_visible = sm.get_picker_max_visible()
            autocomplete_max_visible = sm.get_autocomplete_max_visible()

        if keybindings:
            configure_keybindings(keybindings)

        show_hardware_cursor = False
        editor_padding_x = 0
        if sm is not None:
            show_hardware_cursor = sm.get_show_hardware_cursor()
            editor_padding_x = sm.get_editor_padding_x()

        tui = TUI(show_hardware_cursor=show_hardware_cursor)
        layout = Layout(
            tui,
            theme=resolved_theme,
            picker_max_visible=picker_max_visible,
            autocomplete_max_visible=autocomplete_max_visible,
            editor_padding_x=editor_padding_x,
        )
        tui.set_focus(layout)
        app = cls(runtime, tui, layout)

        # ESC clears the editor only while idle; mid-stream it must fall through
        # to the global key handler so it can abort the run.
        layout.set_busy_check(
            lambda: (a := runtime.agent) is not None and not a.is_idle()
        )

        runtime.set_layout(layout)

        tool_registry = getattr(getattr(runtime, "_context", None), "tool_registry", None)
        if tool_registry is not None:
            layout.messages.set_tool_lookup(tool_registry.get)

        ext = runtime.extension_runtime
        if ext is not None:
            from tau.tui.message_renderers import message_renderer_registry
            for ctype, fn in ext.get_message_renderers().items():
                message_renderer_registry.register(ctype, fn)
            for provider in ext.get_autocomplete_providers():
                layout.register_autocomplete_provider(provider)
        return app

    @classmethod
    async def from_config(
        cls,
        config: RuntimeConfig,
        theme: LayoutTheme | str | None = None,
        keybindings: KeyMap | None = None,
    ) -> App:
        """Convenience: build Runtime from config then attach the TUI."""
        from tau.runtime.service import Runtime
        runtime = await Runtime.create(config)
        return await cls.create(runtime, theme=theme, keybindings=keybindings)

    # -------------------------------------------------------------------------
    # Command context
    # -------------------------------------------------------------------------

    def _ctx(self) -> CommandContext:
        return CommandContext(
            runtime=self._runtime,
            layout=self._layout,
            tui=self._tui,
            on_palette_refresh=self.refresh_palette,
        )

    def _get_current_model_key(self) -> str:
        agent = self._runtime.agent
        if agent is None:
            return ""
        llm = getattr(getattr(agent, "_engine", None), "llm", None)
        model = getattr(llm, "model", None) if llm is not None else None
        if model is None:
            return ""
        return f"{model.provider}/{model.id}"

    def _on_model_palette_commit(self, model_id: str, provider: str) -> None:
        import asyncio
        from tau.tui.commands import model as cmd_model
        asyncio.ensure_future(cmd_model._apply_model(self._ctx(), model_id, provider))

    # -------------------------------------------------------------------------
    # UI command registration
    # -------------------------------------------------------------------------

    def _register_ui_commands(self) -> None:
        from tau.commands.types import CommandInfo
        from tau.tui.commands import model as cmd_model
        from tau.tui.commands import appearance as cmd_appearance
        from tau.tui.commands import session as cmd_session
        from tau.tui.commands import auth as cmd_auth
        from tau.tui.commands import misc as cmd_misc

        reg = [
            CommandInfo(name="model",    description="Switch the active model (interactive picker).",
                        call=lambda _r, _a: cmd_model.open_model_selector(self._ctx()),
                        ),
            CommandInfo(name="effort",   description="Set the thinking effort level for the current model.",
                        call=lambda _r, _a: cmd_model.open_effort_selector(self._ctx())),
            CommandInfo(name="theme",    description="Change the UI theme (interactive picker).",
                        call=lambda _r, _a: cmd_appearance.open_theme_selector(self._ctx())),
            CommandInfo(name="settings", description="Show current settings.",
                        call=lambda _r, _a: cmd_appearance.open_settings_panel(self._ctx())),
            CommandInfo(name="resume",   description="Browse and resume a past session interactively.",
                        call=lambda _r, _a: cmd_session.open_resume_selector(self._ctx())),
            CommandInfo(name="tree",     description="Navigate the session tree and switch to a different branch.",
                        call=lambda _r, _a: cmd_session.open_tree_selector(self._ctx())),
            CommandInfo(name="clone",    description="Duplicate the current session at the current position.",
                        call=lambda _r, _a: cmd_session.cmd_clone(self._ctx())),
            CommandInfo(name="session",  description="Show session info and stats.",
                        call=lambda _r, _a: cmd_session.cmd_session(self._ctx())),
            CommandInfo(name="login",    description="Save an API key for a provider.",
                        call=lambda _r, _a: cmd_auth.open_login_selector(self._ctx())),
            CommandInfo(name="logout",   description="Remove stored credentials for a provider.",
                        call=lambda _r, _a: cmd_auth.open_logout_selector(self._ctx())),
            CommandInfo(name="copy",     description="Copy the last assistant message to the clipboard.",
                        call=lambda _r, _a: cmd_misc.cmd_copy(self._ctx())),
            CommandInfo(name="help",     description="List all commands and keyboard shortcuts.",
                        call=lambda _r, _a: cmd_misc.show_help(self._ctx()),
                        aliases=["?"]),
            CommandInfo(name="quit",     description="Exit tau.",
                        call=lambda _r, _a: self._tui.stop(),
                        aliases=["q", "exit"]),
        ]
        for info in reg:
            self._runtime.commands.register(info)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def _redirect_logging_off_terminal(self) -> None:
        """Keep all logging off the terminal while the TUI owns the screen.

        The renderer tracks the screen with a differential model; any bytes
        written to the terminal by something other than the renderer desync it
        and leave stale lines (e.g. a stranded spinner). Without an explicit
        handler, Python's ``logging.lastResort`` writes WARNING+ records to
        stderr — and the LSP client logs the language server's stderr at WARNING
        on every read. Route everything to a log file instead and neutralise the
        stderr fallback so nothing reaches the TTY.
        """
        import logging
        import sys
        from tau.settings.paths import get_logs_dir
        from tau.session.utils import create_session_id

        root = logging.getLogger()
        # Drop any handler that writes to the live terminal (e.g. --debug's
        # basicConfig stderr handler) — it would corrupt the renderer.
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (sys.stdout, sys.stderr):
                root.removeHandler(h)
        # Unconfigured loggers must never fall back to the stderr last-resort.
        logging.lastResort = logging.NullHandler()
        # One log file per run, named by the active session id so logs don't grow
        # unbounded in a single file. Fall back to a fresh id if no session yet.
        sm = self._runtime.session_manager
        log_id = (sm.session_id if sm is not None else None) or create_session_id()
        try:
            logs_dir = get_logs_dir()
            logs_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(logs_dir / f"{log_id}.log")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(fh)
            if root.level == logging.NOTSET or root.level > logging.WARNING:
                root.setLevel(logging.WARNING)
        except OSError:
            # Couldn't open the log file — at least keep logs off the terminal.
            root.addHandler(logging.NullHandler())

    async def run(self) -> None:
        """Set up hooks, replay session, then run the TUI loop."""
        self._redirect_logging_off_terminal()
        self._hooks.subscribe()

        sm = self._runtime.settings_manager
        if sm is None or not sm.get_quiet_startup():
            self._replay_session()

        self._hooks._refresh_model_badge()
        self._input.load_history()

        self._register_ui_commands()
        self._layout.set_commands(self._build_palette_entries())
        self._layout.set_model_callbacks(
            commit_cb=self._on_model_palette_commit,
            current_key_cb=self._get_current_model_key,
        )

        sm = self._runtime.session_manager
        if sm is not None:
            self._layout.set_cwd(sm.cwd)

        self._input.bind()
        self._tui.on_input(self._on_global_key)
        self._register_extension_shortcuts()

        # Fire tui_ready so extensions can run initial UI setup now that the
        # layout exists (session_start fires earlier, before the layout is set).
        from tau.hooks.tui import TuiReadyEvent, TuiStartEvent, TuiExitEvent
        await self._runtime.hooks.emit(TuiReadyEvent())

        # If the project needs a trust decision, replace the root with TrustScreen
        # before the loop starts so the layout never renders until the user acts.
        trust_needed = self._setup_trust_screen_if_needed()

        asyncio.ensure_future(self._announce_update())

        await self._runtime.hooks.emit(TuiStartEvent())
        try:
            await self._tui.run()
        finally:
            await self._runtime.hooks.emit(TuiExitEvent())
            self._cleanup()

    # -------------------------------------------------------------------------
    # Project trust prompt
    # -------------------------------------------------------------------------

    def _setup_trust_screen_if_needed(self) -> bool:
        """If the project needs a trust decision, swap the TUI root to TrustScreen.

        Returns True if the trust screen was installed (caller can ignore the value).
        The trust screen schedules its own async resolution and swaps back to the
        normal layout once the user acts.
        """
        sm = self._runtime.settings_manager
        if sm is None or sm.is_project_trusted():
            return False
        session_mgr = self._runtime.session_manager
        if session_mgr is None:
            return False
        cwd = session_mgr.cwd

        from tau.trust.manager import has_project_trust_inputs, get_trust_options, trust_store, TrustOption
        if not has_project_trust_inputs(cwd):
            return False

        options = get_trust_options(cwd, session_only=True)

        def _on_commit(chosen: TrustOption | None) -> None:
            if chosen is None or not chosen.trusted:
                # User declined trust (or cancelled) — exit instead of
                # falling through to the normal agent layout.
                self._tui.stop()
                return

            # Restore the normal layout now that the project is trusted
            self._tui.clear()
            self._layout.attach(self._tui)
            self._tui.set_focus(self._layout)
            self._tui.request_render()

            trust_store.apply_option(chosen)
            sm.set_project_trusted(True)

            # Reload extensions so project config takes effect
            import asyncio as _asyncio
            async def _reload() -> None:
                await self._runtime.reload_extensions()
            _asyncio.ensure_future(_reload())

        from tau.tui.components.trust_screen import TrustScreen
        screen = TrustScreen(str(cwd), options, _on_commit)
        self._layout.detach(self._tui)
        self._tui.add_child(screen)
        self._tui.set_focus(screen)
        return True

    # -------------------------------------------------------------------------
    # Global key handler
    # -------------------------------------------------------------------------

    def _on_global_key(self, event: InputEvent) -> None:
        if not isinstance(event, KeyEvent):
            return

        if event.matches("escape"):
            import time
            agent = self._runtime.agent
            if agent is not None and not agent.is_idle():
                self._input.escape_abort()
                self._last_escape = 0.0
            elif not self._layout.input.text:
                # Double-escape on empty editor: perform the configured action
                now = time.monotonic()
                if now - self._last_escape < 0.5:
                    self._last_escape = 0.0
                    self._do_double_escape()
                else:
                    self._last_escape = now
            else:
                self._last_escape = 0.0
            return

        if event.matches("ctrl+c"):
            import time
            agent = self._runtime.agent
            if agent is not None and not agent.is_idle():
                agent.abort()
            else:
                now = time.monotonic()
                if now - self._last_ctrl_c < 0.5:
                    self._tui.stop()
                else:
                    self._last_ctrl_c = now
                    self._layout.input.clear()
                    self._tui.request_render()
            return

        if event.matches("ctrl+o"):
            self._layout.messages.toggle_tool_results_expanded()
            self._tui.request_render()
            return

        if event.matches("ctrl+e"):
            self._layout.messages.toggle_invocations_expanded()
            self._tui.request_render()
            return

        if event.matches("ctrl+d"):
            self._tui.stop()

    def _do_double_escape(self) -> None:
        """Execute the action configured for double-Escape on an empty editor."""
        import asyncio
        sm = self._runtime.settings_manager
        action = sm.get_double_escape_action() if sm is not None else "fork"
        match action:
            case "none":
                return
            case "tree":
                from tau.tui.commands import session as cmd_session
                cmd_session.open_tree_selector(self._ctx())
            case "fork" | _:
                from tau.tui.commands import session as cmd_session
                cmd_session.cmd_clone(self._ctx())

    # -------------------------------------------------------------------------
    # Extension shortcuts
    # -------------------------------------------------------------------------

    def _register_extension_shortcuts(self) -> None:
        runtime = self._runtime
        for shortcut in runtime.extension_shortcuts:
            key = shortcut.key
            handler = shortcut.handler

            def _make_handler(k, h):
                def on_input(event: object) -> None:
                    if not isinstance(event, KeyEvent) or not event.matches(k):
                        return
                    ctx = ExtensionContext.from_runtime(runtime)
                    result = h(ctx)
                    if asyncio.iscoroutine(result):
                        asyncio.ensure_future(result)  # type: ignore[arg-type]
                return on_input

            self._unsubs.append(self._tui.on_input(_make_handler(key, handler)))

    # -------------------------------------------------------------------------
    # Startup helpers
    # -------------------------------------------------------------------------

    def _build_palette_entries(self):
        from tau.commands.types import CommandInfo
        from tau.prompts.registry import prompt_registry

        # Commands whose feature is currently switched off are hidden from the
        # palette (and treated as unavailable) for this session.
        sm = self._runtime.settings_manager
        hidden: set[str] = set()
        if sm is not None and not sm.is_compaction_enabled():
            hidden.add("compact")

        overrides = self._palette_dynamic_descriptions()
        entries = []
        for cmd in self._runtime.commands.list():
            if cmd.name in hidden:
                continue
            if cmd.name in overrides:
                from dataclasses import replace
                entries.append(replace(cmd, description=overrides[cmd.name]))
            else:
                entries.append(cmd)
        for tmpl in prompt_registry.list():
            hint = f"  {tmpl.argument_hint}" if tmpl.argument_hint else ""
            entries.append(CommandInfo(
                name=tmpl.name,
                description=tmpl.description + hint,
                call=lambda _r, _a: None,
                argument_hint=tmpl.argument_hint,
            ))
        return entries

    def _palette_dynamic_descriptions(self) -> dict[str, str]:
        from tau.tui.commands import model as cmd_model
        from tau.tui.commands import auth as cmd_auth
        overrides = cmd_model.get_palette_overrides(self._runtime.agent)
        overrides.update(cmd_auth.get_palette_overrides())
        return overrides

    def refresh_palette(self) -> None:
        self._layout.set_commands(self._build_palette_entries())

    def _replay_session(self) -> None:
        sm = self._runtime.session_manager
        if sm is None:
            return
        ctx = sm.build_session_context()
        for msg in ctx.messages:
            self._layout.add_message(msg)

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def _announce_update(self) -> None:
        task = self._runtime.version_check_task
        if task is None:
            return
        latest = await task
        if latest is None:
            return
        from tau.tui.ansi import BOLD, BRIGHT_YELLOW, DIM, RESET
        from tau.tui.component import Column, StaticComponent
        from tau.tui.components.dynamic_border import DynamicBorder
        color = lambda s: BRIGHT_YELLOW + s + RESET  # noqa: E731
        banner = Column([
            DynamicBorder(color),
            StaticComponent([
                f"  {BRIGHT_YELLOW}⚡{RESET} Update available: {BOLD}v{latest}{RESET}"
                f"{DIM}  ·  run: tau update{RESET}",
            ]),
            DynamicBorder(color),
        ])
        self._layout.set_widget("version_update", banner, placement="above_editor")

    def _cleanup(self) -> None:
        self._input.save_history()
        self._hooks.unsubscribe()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
