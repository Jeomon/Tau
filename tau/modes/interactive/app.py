from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.extensions import ExtensionContext, ShortcutRegistration
from tau.modes.interactive.agent_hooks import AgentHookHandler
from tau.modes.interactive.commands.context import CommandContext
from tau.modes.interactive.components.layout import Layout
from tau.modes.interactive.input_handler import InputHandler
from tau.tui.input import (
    InputEvent,
    KeyEvent,
    KeyMap,
    configure_keybindings,
    get_keybindings,
    normalize_key_id,
)
from tau.tui.service import TUI
from tau.tui.theme import LayoutTheme
from tau.tui.utils import project_name

if TYPE_CHECKING:
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig

_log = logging.getLogger(__name__)

# Caps how long a single shutdown-path hook handler or pending background task
# can delay exit. Quit should feel instant; a hung extension handler or a task
# stuck in a non-cancellable blocking call shouldn't be able to stall it.
_SHUTDOWN_TIMEOUT = 2.0

_RESERVED_EXTENSION_SHORTCUT_ACTIONS = frozenset(
    {
        "tui.app.quit",
        "tui.app.abort",
        "tui.input.submit",
        "tui.input.newline",
        "tui.input.clear",
        "tui.input.word_back",
        "tui.select.up",
        "tui.select.down",
        "tui.select.page_up",
        "tui.select.page_down",
        "tui.select.top",
        "tui.select.bottom",
        "tui.select.confirm",
        "tui.select.dismiss",
        "app.message.followup",
        "app.message.dequeue",
        "tui.scroll.up",
        "tui.scroll.down",
        "tui.scroll.top",
        "tui.scroll.bottom",
    }
)


def _resolve_extension_shortcuts(
    shortcuts: list[ShortcutRegistration],
) -> tuple[list[ShortcutRegistration], list[str]]:
    """Resolve raw extension shortcuts against the effective TUI keymap."""
    bindings = get_keybindings().effective_map()
    by_key = {
        normalize_key_id(key): (action, action in _RESERVED_EXTENSION_SHORTCUT_ACTIONS)
        for action, keys in bindings.items()
        for key in keys
    }
    resolved: dict[tuple[frozenset[str], str], ShortcutRegistration] = {}
    warnings: list[str] = []

    for shortcut in shortcuts:
        signature = normalize_key_id(shortcut.key)
        builtin = by_key.get(signature)
        if builtin is not None and builtin[1]:
            warnings.append(
                f"Extension shortcut '{shortcut.key}' from {shortcut.extension_path} "
                f"conflicts with reserved TUI action {builtin[0]}; skipping."
            )
            continue
        if builtin is not None:
            warnings.append(
                f"Extension shortcut '{shortcut.key}' from {shortcut.extension_path} "
                f"overrides TUI action {builtin[0]}."
            )
        previous = resolved.get(signature)
        if previous is not None:
            warnings.append(
                f"Extension shortcut '{shortcut.key}' is registered by both "
                f"{previous.extension_path} and {shortcut.extension_path}; "
                f"using {shortcut.extension_path}."
            )
        resolved[signature] = shortcut

    return list(resolved.values()), warnings


class App:
    """
    Wires the TUI layout to the agent runtime.

    Delegates to focused collaborators:
      - AgentHookHandler  — subscribes to agent events, drives spinner/messages
      - InputHandler      — submit, paste, clipboard, steer, history
      - tau.modes.interactive.commands.* — slash command logic, each receiving a CommandContext

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
            runtime,
            layout,
            tui,
            on_palette_refresh=self.refresh_palette,
            on_turn_content=self._input.mark_turn_content,
            on_settled=self._input.on_settled,
        )
        self._unsubs: list[Callable[[], None]] = []
        self._extension_shortcut_unsubs: list[Callable[[], None]] = []
        self._pending_tasks: set[asyncio.Task] = set()
        self._last_ctrl_c: float = 0.0
        self._last_escape: float = 0.0
        self._saved_log_handlers: list[logging.Handler] | None = None
        self._saved_log_level: int | None = None
        self._saved_last_resort: logging.Handler | None = None
        self._tui_log_handler: logging.Handler | None = None

        # Auto light/dark: when the theme setting is "auto", the active theme is
        # refined from the terminal background colour once it's known at runtime.
        self._auto_theme: bool = False
        self._theme_name: str = "dark"

        # True only on the very first launch (no settings file existed at
        # startup) — shows the one-time theme/telemetry setup screen.
        self._first_run_setup: bool = False

    # -------------------------------------------------------------------------
    # Theme
    # -------------------------------------------------------------------------

    @staticmethod
    def _apply_message_flags(theme: LayoutTheme, sm: Any) -> None:
        """Re-apply the user's message-display prefs onto a (possibly swapped) theme."""
        if sm is not None:
            theme.message.show_thinking = sm.get_show_thinking()
            theme.message.show_tool_calls = sm.get_show_tool_calls()
            theme.message.show_images = sm.get_show_images()

    def _on_terminal_background(self, color: tuple[int, int, int] | None) -> None:
        """Auto-select the light/dark builtin theme from the terminal background.

        Fires once at startup (via ``TUI.on_background_color``) when the theme
        setting is ``"auto"``. No reply → keep the provisional default.
        """
        if color is None:
            return
        from tau.themes.registry import mode_for_background, theme_registry

        mode = mode_for_background(color)
        if mode == self._theme_name:
            return
        try:
            new_theme = theme_registry.get(mode)
        except ValueError:
            return
        self._apply_message_flags(new_theme, self._runtime.settings_manager)
        self._theme_name = mode
        self._layout.set_theme(new_theme)

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        runtime: Runtime,
        theme: LayoutTheme | str | None = None,
        keybindings: KeyMap | None = None,
        first_run_setup: bool = False,
    ) -> App:
        """Build the TUI around an already-constructed Runtime."""
        from tau.themes.registry import DEFAULT_THEME, theme_registry

        resolved_theme: LayoutTheme | None
        theme_name = DEFAULT_THEME
        auto_theme = False
        if isinstance(theme, LayoutTheme):
            resolved_theme = theme
        else:
            if isinstance(theme, str):
                requested = theme
            else:
                _sm = runtime.settings_manager
                requested = (_sm.get_theme() if _sm is not None else None) or DEFAULT_THEME
            # "auto" selects light/dark from the terminal background at runtime;
            # start on the default theme until the OSC 11 reply arrives.
            auto_theme = requested == "auto"
            theme_name = DEFAULT_THEME if auto_theme else requested
            try:
                resolved_theme = theme_registry.get(theme_name)
            except ValueError:
                # Configured theme is gone (e.g. an uninstalled theme package)
                # or the default builtin is missing — fall back to a theme that
                # is guaranteed to load instead of crashing on startup.
                theme_name = DEFAULT_THEME
                resolved_theme = theme_registry.get_default()

        sm = runtime.settings_manager
        picker_max_visible = 8
        autocomplete_max_visible = 5
        tool_result_preview_lines = 5
        cls._apply_message_flags(resolved_theme, sm)
        if sm is not None:
            picker_max_visible = sm.get_picker_max_visible()
            autocomplete_max_visible = sm.get_autocomplete_max_visible()
            tool_result_preview_lines = sm.get_tool_result_preview_lines()

        if keybindings:
            configure_keybindings(keybindings)

        show_hardware_cursor = False
        editor_padding_x = 0
        cursor_blink = True
        if sm is not None:
            show_hardware_cursor = sm.get_show_hardware_cursor()
            editor_padding_x = sm.get_editor_padding_x()
            cursor_blink = sm.get_cursor_blink()

        tui = TUI(
            show_hardware_cursor=show_hardware_cursor,
            title=f"τ - {project_name()}",
        )
        # tau.tui is a standalone package with no tau.* imports of its own (see
        # tests/test_tui_public_api.py), so profiling is injected from here
        # rather than imported inside it. profiling.span is a no-op factory
        # unless TAU_PROFILE=1, so this is always safe to set.
        from tau.tui.service import set_span_hook
        from tau.utils import profiling

        set_span_hook(profiling.span)
        if resolved_theme.terminal_bg:
            tui.terminal_bg = resolved_theme.terminal_bg
        layout = Layout(
            tui,
            theme=resolved_theme,
            picker_max_visible=picker_max_visible,
            autocomplete_max_visible=autocomplete_max_visible,
            editor_padding_x=editor_padding_x,
            tool_result_preview_lines=tool_result_preview_lines,
            cursor_blink=cursor_blink,
        )
        tui.set_focus(layout)
        app = cls(runtime, tui, layout)
        app._auto_theme = auto_theme
        app._theme_name = theme_name
        app._first_run_setup = first_run_setup

        # ESC clears the editor only while idle; mid-stream it must fall through
        # to the global key handler so it can abort the run.
        layout.set_busy_check(lambda: (a := runtime.agent) is not None and not a.is_idle())

        runtime.set_layout(layout)
        runtime.set_extension_ui_refresh(app._refresh_extension_ui)

        tool_registry = getattr(getattr(runtime, "_context", None), "tool_registry", None)
        if tool_registry is not None:
            layout.messages.set_tool_lookup(tool_registry.get)

        app._refresh_extension_ui()
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

    def _track_task(self, task: asyncio.Task) -> None:
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    # -------------------------------------------------------------------------
    # UI command registration
    # -------------------------------------------------------------------------

    def _register_ui_commands(self) -> None:
        from tau.commands.types import CommandInfo
        from tau.modes.interactive.commands import auth as cmd_auth
        from tau.modes.interactive.commands import extensions as cmd_extensions
        from tau.modes.interactive.commands import misc as cmd_misc
        from tau.modes.interactive.commands import model as cmd_model
        from tau.modes.interactive.commands import session as cmd_session
        from tau.modes.interactive.commands import settings as cmd_settings

        reg = [
            CommandInfo(
                name="model",
                description="Switch models for any modality (text/voice/speak/image/video).",
                call=lambda _r, _a: cmd_model.open_model_selector(
                    self._ctx(), _a[0] if _a else None
                ),
                argument_hint="[text|voice|speak|image|video]",
                get_argument_completions=cmd_model.modality_completions,
            ),
            CommandInfo(
                name="effort",
                description="Set the thinking effort level for the current model.",
                call=lambda _r, _a: cmd_model.open_effort_selector(self._ctx()),
            ),
            CommandInfo(
                name="theme",
                description="Change the UI theme (interactive picker).",
                call=lambda _r, _a: cmd_settings.open_theme_selector(self._ctx()),
                requires_idle=False,
            ),
            CommandInfo(
                name="settings",
                description="Show current settings.",
                call=lambda _r, _a: cmd_settings.open_settings_panel(self._ctx()),
                requires_idle=False,
            ),
            CommandInfo(
                name="extensions",
                description="Enable or disable extensions by scope.",
                call=lambda _r, _a: cmd_extensions.open_config_panel(self._ctx()),
            ),
            CommandInfo(
                name="resume",
                description="Browse and resume a past session interactively.",
                call=lambda _r, _a: cmd_session.open_resume_selector(self._ctx()),
            ),
            CommandInfo(
                name="tree",
                description="Navigate the session tree and switch to a different branch.",
                call=lambda _r, _a: cmd_session.open_tree_selector(self._ctx()),
            ),
            CommandInfo(
                name="clone",
                description="Duplicate the current session at the current position.",
                call=lambda _r, _a: cmd_session.cmd_clone(self._ctx()),
            ),
            CommandInfo(
                name="session",
                description="Show session info and stats.",
                call=lambda _r, _a: cmd_session.cmd_session(self._ctx()),
                requires_idle=False,
            ),
            CommandInfo(
                name="login",
                description="Save an API key for a provider.",
                call=lambda _r, _a: cmd_auth.open_login_selector(self._ctx()),
            ),
            CommandInfo(
                name="logout",
                description="Remove stored credentials for a provider.",
                call=lambda _r, _a: cmd_auth.open_logout_selector(self._ctx()),
            ),
            CommandInfo(
                name="copy",
                description="Copy the last assistant message to the clipboard.",
                call=lambda _r, _a: cmd_misc.cmd_copy(self._ctx()),
                requires_idle=False,
            ),
            CommandInfo(
                name="help",
                description="List all commands and keyboard shortcuts.",
                call=lambda _r, _a: cmd_misc.show_help(self._ctx()),
                aliases=["?"],
                requires_idle=False,
            ),
            CommandInfo(
                name="quit",
                description="Exit tau.",
                call=lambda _r, _a: self._tui.stop(),
                aliases=["q", "exit"],
            ),
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

        from tau.session.utils import create_session_id
        from tau.settings.paths import get_logs_dir

        root = logging.getLogger()
        if self._saved_log_handlers is None:
            self._saved_log_handlers = list(root.handlers)
            self._saved_log_level = root.level
            self._saved_last_resort = logging.lastResort
        # Drop any handler that writes to the live terminal (e.g. --debug's
        # basicConfig stderr handler) — it would corrupt the renderer.
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (
                sys.stdout,
                sys.stderr,
            ):
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
            self._tui_log_handler = fh
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
        sm = self._runtime.session_manager
        if sm is not None:
            self._layout.set_cwd(sm.cwd)

        self._input.bind()
        self._register_extension_shortcuts()
        self._tui.on_input(self._on_global_key)

        # Fire tui_ready so extensions can run initial UI setup now that the
        # layout exists (session_start fires earlier, before the layout is set).
        from tau.hooks.tui import TuiExitEvent, TuiReadyEvent, TuiStartEvent

        await self._runtime.hooks.emit(TuiReadyEvent())

        # On the very first launch the root becomes the one-time setup screen,
        # which chains into the trust prompt itself once the user acts. Either
        # way the layout never renders until every pending decision is made.
        if not self._setup_first_run_screen_if_needed():
            self._setup_trust_screen_if_needed()

        self._track_task(asyncio.ensure_future(self._announce_update()))

        if self._auto_theme:
            self._tui.on_background_color = self._on_terminal_background

        await self._runtime.hooks.emit(TuiStartEvent())
        try:
            await self._tui.run()
        finally:
            await self._runtime.hooks.emit(TuiExitEvent(), timeout=_SHUTDOWN_TIMEOUT)
            await self._cleanup()

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

        from tau.trust.manager import (
            TrustOption,
            get_trust_options,
            has_project_trust_inputs,
            trust_store,
        )

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

            # Now that trust is granted, start persisting the session.
            session_mgr = self._runtime.session_manager
            if session_mgr is not None and not session_mgr.persist:
                session_mgr.enable_persist()

            # Reload extensions so project config takes effect
            import asyncio as _asyncio

            async def _reload() -> None:
                await self._runtime.reload_extensions()

            self._track_task(_asyncio.ensure_future(_reload()))

        from tau.modes.interactive.components.trust_screen import TrustScreen

        screen = TrustScreen(str(cwd), options, _on_commit, theme=self._layout.theme)
        self._layout.detach(self._tui)
        self._tui.add_child(screen)
        self._tui.set_focus(screen)
        return True

    # -------------------------------------------------------------------------
    # First-run setup
    # -------------------------------------------------------------------------

    def _setup_first_run_screen_if_needed(self) -> bool:
        """On the very first launch, swap the TUI root to FirstRunScreen.

        Returns True if the screen was installed. The commit callback restores
        the normal layout, persists the choices (which creates the settings
        file, so the screen never shows again), and then runs the trust check
        that was skipped in run(). Esc skips without persisting anything.
        """
        if not self._first_run_setup:
            return False
        sm = self._runtime.settings_manager
        if sm is None:
            return False

        import contextlib

        from tau.modes.interactive.components.first_run_screen import (
            FirstRunResult,
            FirstRunScreen,
        )
        from tau.themes.registry import AUTO_THEME, mode_for_background, theme_registry

        original_theme = self._layout.theme

        def _resolve(name: str) -> str:
            # Map "auto" to the concrete builtin for the terminal background.
            # The OSC 11 reply is queried unconditionally early in TUI.run(),
            # so by the time the user navigates it has normally arrived.
            if name == AUTO_THEME:
                return mode_for_background(self._tui.background_color)
            return name

        def _preview(name: str) -> None:
            with contextlib.suppress(ValueError):
                new_theme = theme_registry.get(_resolve(name))
                self._apply_message_flags(new_theme, sm)
                self._layout.set_theme(new_theme)
                screen.set_theme(new_theme)
            self._tui.request_render()

        def _on_commit(result: FirstRunResult | None) -> None:
            self._tui.clear()
            self._layout.attach(self._tui)
            self._tui.set_focus(self._layout)

            if result is None:
                # Skipped — restore the startup theme and persist nothing, so
                # setup is offered again on the next launch.
                self._layout.set_theme(original_theme)
            else:
                self._auto_theme = result.theme == AUTO_THEME
                self._theme_name = _resolve(result.theme)
                with contextlib.suppress(ValueError):
                    new_theme = theme_registry.get(self._theme_name)
                    self._apply_message_flags(new_theme, sm)
                    self._layout.set_theme(new_theme)
                sm.set_theme(result.theme)  # persist "auto" verbatim
                sm.set_telemetry(result.share_telemetry)

            self._tui.request_render()
            self._setup_trust_screen_if_needed()

        theme_options = [
            (AUTO_THEME, "Auto — match the terminal background"),
            ("dark", "Dark"),
            ("light", "Light"),
        ]
        screen = FirstRunScreen(theme_options, _preview, _on_commit, theme=self._layout.theme)
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

        keybindings = get_keybindings()
        if keybindings.matches(event, "tui.app.abort"):
            if event.matches("ctrl+c"):
                self._handle_ctrl_c()
            else:
                self._handle_escape()
            return

        if keybindings.matches(event, "tui.app.quit"):
            self._tui.stop()
            return

        if keybindings.matches(event, "app.details.toggle"):
            self._layout.messages.toggle_details_expanded()
            self._tui.request_render()
            return

        if keybindings.matches(event, "app.invocations.toggle"):
            self._layout.messages.toggle_invocations_expanded()
            self._tui.request_render()
            return

        if keybindings.matches(event, "app.editor.external"):
            self._track_task(asyncio.ensure_future(self._open_external_editor()))
            return

    async def _open_external_editor(self) -> None:
        """Compose the prompt in $EDITOR, then bring the text back.

        The editor owns the terminal while it runs, so the TUI is suspended
        around the child rather than stopped. A non-zero exit means the user
        bailed out (``:cq``), and the prompt is left untouched — only a clean
        exit replaces it.
        """
        import os
        import shlex
        import tempfile

        settings = self._runtime.settings_manager
        if settings is None:
            return
        command = settings.get_external_editor_command()

        text = self._layout.get_editor_text()
        # .md so the editor lights up markdown; the prompt is markdown in practice.
        fd, tmp_path = tempfile.mkstemp(prefix="tau-editor-", suffix=".tau.md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)

            # shlex so "code --wait" works *and* a quoted path with spaces
            # survives ("C:\\Program Files\\...\\subl.exe" --wait). Windows
            # keeps backslashes literal, so it must not use POSIX escaping.
            parts = shlex.split(command, posix=os.name != "nt")
            if not parts:
                return
            try:
                async with self._tui.suspended():
                    proc = await asyncio.create_subprocess_exec(*parts, tmp_path)
                    returncode = await proc.wait()
            except (OSError, ValueError) as exc:
                self._ctx().notify(
                    f"Could not launch external editor {command!r}: {exc}. "
                    "Set `external_editor` in settings.json, or $VISUAL/$EDITOR."
                )
                return

            if returncode == 0:
                with open(tmp_path, encoding="utf-8") as handle:
                    edited = handle.read()
                # Editors add a trailing newline on save; one is an artefact,
                # more than one the user meant.
                self._layout.set_editor_text(edited[:-1] if edited.endswith("\n") else edited)
                self._tui.request_render()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    def _handle_escape(self) -> None:
        import time

        agent = self._runtime.agent
        if agent is not None and not agent.is_idle():
            self._input.escape_abort()
            self._last_escape = 0.0
        else:
            now = time.monotonic()
            if now - self._last_escape < 0.5:
                self._last_escape = 0.0
                self._do_double_escape()
            else:
                self._last_escape = now

    def _handle_ctrl_c(self) -> None:
        import time

        agent = self._runtime.agent
        if agent is not None and not agent.is_idle():
            agent.abort()
            return
        now = time.monotonic()
        if now - self._last_ctrl_c < 0.5:
            self._tui.stop()
            return
        self._last_ctrl_c = now
        self._layout.input.clear()
        self._tui.request_render()

    def _do_double_escape(self) -> None:
        """Execute the action configured for double-Escape on an empty editor."""

        sm = self._runtime.settings_manager
        action = sm.get_double_escape_action() if sm is not None else "clear"
        match action:
            case "none":
                return
            case "clear":
                self._layout.clear_messages()
                self._tui.request_render()
            case "tree":
                from tau.modes.interactive.commands import session as cmd_session

                cmd_session.open_tree_selector(self._ctx())
            case "fork":
                from tau.modes.interactive.commands import session as cmd_session

                cmd_session.cmd_clone(self._ctx())
            case _:
                self._layout.clear_messages()
                self._tui.request_render()

    # -------------------------------------------------------------------------
    # Extension shortcuts
    # -------------------------------------------------------------------------

    def _register_extension_shortcuts(self) -> None:
        runtime = self._runtime
        for unsub in self._extension_shortcut_unsubs:
            unsub()
        self._extension_shortcut_unsubs.clear()
        shortcuts, warnings = _resolve_extension_shortcuts(runtime.extension_shortcuts)
        for warning in warnings:
            _log.warning(warning)
            self._ctx().notify(warning)

        for shortcut in shortcuts:
            key = shortcut.key
            handler = shortcut.handler

            def _make_handler(k, h):
                def on_input(event: object) -> bool:
                    if not isinstance(event, KeyEvent) or not event.matches(k):
                        return False
                    ctx = ExtensionContext.from_runtime(runtime)
                    result = h(ctx)
                    if asyncio.iscoroutine(result):
                        self._track_task(asyncio.ensure_future(result))  # type: ignore[arg-type]
                    return True

                return on_input

            self._extension_shortcut_unsubs.append(
                self._tui.on_input(_make_handler(key, handler), prepend=True)
            )

    def _refresh_extension_ui(self) -> None:
        """Replace extension renderers, completions, shortcuts, and palette after reload."""
        from tau.tui.markdown import message_renderer_registry

        ext = self._runtime.extension_runtime
        renderers = ext.get_message_renderers() if ext is not None else {}
        providers = ext.get_autocomplete_providers() if ext is not None else []
        message_renderer_registry.replace(renderers)
        self._layout.replace_autocomplete_providers(providers)
        # Rebuild the "/" command palette too: reload swaps the extension source
        # in self._runtime.commands (so a new command already *runs*), but the
        # palette holds a startup-time snapshot of that list — without this a
        # newly added extension's command stays invisible in autocomplete until
        # a full restart.
        self.refresh_palette()
        if self._tui._running:
            self._register_extension_shortcuts()
        self._tui.request_render()

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
            entries.append(
                CommandInfo(
                    name=tmpl.name,
                    description=tmpl.description + hint,
                    call=lambda _r, _a: None,
                    argument_hint=tmpl.argument_hint,
                )
            )
        return entries

    def _palette_dynamic_descriptions(self) -> dict[str, str]:
        from tau.modes.interactive.commands import auth as cmd_auth
        from tau.modes.interactive.commands import model as cmd_model

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
        from tau.tui.component import Column, StaticComponent
        from tau.tui.components.box import DynamicBorder
        from tau.tui.style import apply_style
        from tau.tui.utils import BOLD, RESET

        theme = self._layout.theme
        banner = Column(
            [
                DynamicBorder(theme.warning),
                StaticComponent(
                    [
                        f"  {apply_style(theme.warning, '⚡')} Update available: "
                        f"{BOLD}v{latest}{RESET}"
                        f"{apply_style(theme.muted, '  ·  run: tau update')}",
                    ]
                ),
                DynamicBorder(theme.warning),
            ]
        )
        self._layout.set_widget("version_update", banner, placement="above_editor")

    async def _cleanup(self) -> None:
        self._input.save_history()
        self._input.shutdown()
        self._hooks.unsubscribe()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for unsub in self._extension_shortcut_unsubs:
            unsub()
        self._extension_shortcut_unsubs.clear()
        for task in self._pending_tasks:
            task.cancel()
        if self._pending_tasks:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*self._pending_tasks, return_exceptions=True),
                    _SHUTDOWN_TIMEOUT,
                )
        self._pending_tasks.clear()
        self._tui.dispose()
        sm = self._runtime.settings_manager
        if sm is not None:
            await sm.flush()
        self._restore_logging()
        self._print_resume_hint()

    def _restore_logging(self) -> None:
        """Restore process-global logging configuration changed for TUI rendering."""
        if self._saved_log_handlers is None:
            return
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if handler is self._tui_log_handler:
                handler.close()
        for handler in self._saved_log_handlers:
            root.addHandler(handler)
        if self._saved_log_level is not None:
            root.setLevel(self._saved_log_level)
        logging.lastResort = self._saved_last_resort
        self._saved_log_handlers = None
        self._tui_log_handler = None

    def _print_resume_hint(self) -> None:
        session_mgr = self._runtime.session_manager
        if session_mgr is None or not session_mgr.persist or not session_mgr.session_id:
            return
        # Only show if the session file exists on disk — an empty session that was
        # never written produces an ID that --resume <id> cannot resolve.
        if session_mgr.session_file is None or not session_mgr.session_file.exists():
            return
        sid = session_mgr.session_id
        print("\n\x1b[2mResume this session with:\x1b[0m")
        print(f"\x1b[1mtau --resume {sid}\x1b[0m\n")
