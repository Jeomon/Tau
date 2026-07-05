from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.modes.interactive.components.layout import Layout
    from tau.settings.manager import SettingsManager
    from tau.tui.buffer import Buffer
    from tau.tui.component import Component
    from tau.tui.geometry import Rect
    from tau.tui.input import InputEvent
    from tau.tui.theme import LayoutTheme


@dataclass
class FooterData:
    """Live data passed to enhanced footer factories as the third argument.

    Factories with signature ``(tui, theme, footer_data)`` receive this on
    every render so they can display up-to-date stats without polling.
    """

    git_branch: str = ""
    context_tokens: int | None = None
    context_window: int = 0
    context_percent: float | None = None
    active_extensions: list[str] = field(default_factory=list)
    model_id: str = ""
    provider_id: str = ""


class _NullHandle:
    """Returned by show_overlay when there is no active layout (headless mode)."""

    def close(self) -> None:
        pass

    hide = close


class _InterceptComponent:
    """Wraps a Component and runs an on_handle interceptor before its handle_input.

    Intentionally does not subclass Component to avoid the ABC machinery —
    duck-typing is sufficient since TUI only calls render/handle_input/invalidate.
    """

    def __init__(
        self,
        inner: Component,
        on_handle: Callable[[InputEvent], bool | None],
    ) -> None:
        self._inner = inner
        self._on_handle = on_handle

    def render(self, width: int) -> list[str]:
        return self._inner.render(width)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        return self._inner.render_cells(area, buf)

    def handle_input(self, event: InputEvent) -> bool:
        if self._on_handle(event) is True:
            return True
        return self._inner.handle_input(event)

    def invalidate(self) -> None:
        self._inner.invalidate()


class UIContext:
    """
    Runtime TUI customization API — available as ``ctx.ui`` inside extension handlers.

    Allows extensions to inject widgets, replace the footer, add status slots,
    and replace the input editor — all on the live layout.
    """

    def __init__(self, layout: Layout, settings: SettingsManager | None = None) -> None:
        self._layout_ref: weakref.ref[Layout] = weakref.ref(layout)
        self._settings = settings

    def _layout(self) -> Layout | None:
        return self._layout_ref()

    # -------------------------------------------------------------------------
    # Widgets
    # -------------------------------------------------------------------------

    def set_widget(
        self,
        id: str,
        widget: Component | list[str],
        placement: str = "above_editor",
    ) -> None:
        """Inject a widget above or below the editor. placement: 'above_editor' | 'below_editor'."""
        layout = self._layout()
        if layout is not None:
            layout.set_widget(id, widget, placement)

    def remove_widget(self, id: str) -> None:
        """Remove a previously added widget by id."""
        layout = self._layout()
        if layout is not None:
            layout.remove_widget(id)

    # -------------------------------------------------------------------------
    # Footer
    # -------------------------------------------------------------------------

    def set_footer(self, component_or_factory: Component | Callable[..., Component]) -> None:
        """Replace the footer with a custom component or factory.

        Three factory signatures are supported:

        - ``factory()`` — zero-argument
        - ``factory(tui, theme)`` — receives TUI and theme objects
        - ``factory(tui, theme, footer_data)`` — also receives live ``FooterData``

        Pass ``None`` to revert to the built-in footer.
        """
        import inspect

        layout = self._layout()
        if layout is None:
            return
        if callable(component_or_factory):
            try:
                sig = inspect.signature(component_or_factory)
                arity = len(sig.parameters)
            except (ValueError, TypeError):
                arity = 0
            if arity >= 3:
                footer_data = self._build_footer_data(layout)
                tui = getattr(layout, "_tui", None)
                theme = getattr(layout, "_theme", None)
                component = component_or_factory(tui, theme, footer_data)
            elif arity >= 2:
                tui = getattr(layout, "_tui", None)
                theme = getattr(layout, "_theme", None)
                component = component_or_factory(tui, theme)
            else:
                component = component_or_factory()
        else:
            component = component_or_factory
        layout.set_custom_footer(component)

    @staticmethod
    def _build_footer_data(layout: Layout) -> FooterData:
        """Collect live stats to populate a FooterData for enhanced footer factories."""
        git_branch = ""
        footer = getattr(layout, "footer", None)
        if footer is not None:
            git_branch = getattr(footer, "_git_branch", "") or ""

        context_tokens: int | None = None
        context_window = 0
        context_percent: float | None = None
        tui = getattr(layout, "_tui", None)
        runtime = getattr(tui, "_runtime", None) if tui is not None else None
        agent = getattr(runtime, "agent", None) if runtime is not None else None
        if agent is not None:
            usage_fn = getattr(agent, "get_context_usage", None)
            if callable(usage_fn):
                usage = usage_fn()
                if usage is not None:
                    context_tokens = getattr(usage, "tokens", None)
                    context_window = getattr(usage, "context_window", 0) or 0
                    context_percent = getattr(usage, "percent", None)

        model_id = ""
        provider_id = ""
        if agent is not None:
            engine = getattr(agent, "_engine", None)
            if engine is not None:
                llm = getattr(engine, "llm", None)
                if llm is not None:
                    model_id = getattr(getattr(llm, "model", None), "id", "") or ""
                    provider_id = getattr(llm, "provider_id", "") or ""

        active_extensions: list[str] = []
        if runtime is not None:
            ext_mgr = getattr(runtime, "extension_manager", None)
            if ext_mgr is not None:
                exts = getattr(ext_mgr, "extensions", None) or []
                active_extensions = [getattr(e, "name", "") for e in exts if getattr(e, "name", "")]

        return FooterData(
            git_branch=git_branch,
            context_tokens=context_tokens,
            context_window=context_window,
            context_percent=context_percent,
            active_extensions=active_extensions,
            model_id=model_id,
            provider_id=provider_id,
        )

    def restore_footer(self) -> None:
        """Revert to the built-in FooterBar."""
        layout = self._layout()
        if layout is not None:
            layout.set_custom_footer(None)

    # -------------------------------------------------------------------------
    # Editor replacement
    # -------------------------------------------------------------------------

    def set_editor_component(self, factory: Callable[[Any, Any], Component] | None) -> None:
        """Replace the input editor, or pass None to restore the default.

        Factory receives ``(InputTheme, keybindings)`` and returns a Component.
        """
        layout = self._layout()
        if layout is not None:
            layout.set_custom_input(factory)

    def get_editor_component(self) -> Callable[[Any, Any], Component] | None:
        """Return the currently installed custom editor factory, or None when using the default."""
        layout = self._layout()
        if layout is None:
            return None
        return getattr(layout, "_custom_input_factory", None)

    # -------------------------------------------------------------------------
    # Overlay / interactive dialogs (async — usable from async command handlers)
    # -------------------------------------------------------------------------

    async def select(self, title: str, options: list[str]) -> str | None:
        """Show a picker and return the chosen option, or None if cancelled.

        Usage::

            async def handler(ctx, args):
                choice = await ctx.ui.select("Pick one", ["Foo", "Bar"])
                if choice:
                    ctx.notify(f"You picked {choice}")
        """
        layout = self._layout()
        if layout is None:
            return None
        from tau.tui.components.select_list import SelectItem

        items: list[SelectItem[str]] = [
            SelectItem(label=o, description=title if i == 0 else "", value=o)
            for i, o in enumerate(options)
        ]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str | None] = loop.create_future()

        def _commit(value: str) -> None:
            if not fut.done():
                fut.set_result(value)

        def _cancel() -> None:
            if not fut.done():
                fut.set_result(None)

        layout.open_tree_selector(items, _commit, _cancel)
        return await fut

    async def confirm(self, title: str, message: str = "") -> bool:
        """Show a Yes/No picker and return True if the user chose Yes.

        Usage::

            async def handler(ctx, args):
                if await ctx.ui.confirm("Delete?", "This cannot be undone."):
                    do_delete()
        """
        label = f"{title}: {message}" if message else title
        result = await self.select(label, ["Yes", "No"])
        return result == "Yes"

    async def prompt(self, label: str, *, secret: bool = False) -> str | None:
        """Show a single-line text input overlay and return the entered text, or None if cancelled.

        Usage::

            async def handler(ctx, args):
                key = await ctx.ui.prompt("Enter API key", secret=True)
                if key:
                    save_key(key)
        """
        layout = self._layout()
        if layout is None:
            return None
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str | None] = loop.create_future()

        def _commit(value: str) -> None:
            if not fut.done():
                fut.set_result(value)

        def _cancel() -> None:
            if not fut.done():
                fut.set_result(None)

        layout.open_prompt(label, _commit, _cancel, secret=secret)
        return await fut

    async def editor(self, title: str, prefill: str = "") -> str | None:
        """Show a floating multi-line text editor.  Returns the saved text, or None if cancelled.

        The editor opens as a full-screen-width overlay.  The user presses
        ``Ctrl+S`` to confirm or ``Escape`` to cancel.

        Usage::

            async def handler(ctx, args):
                text = await ctx.ui.editor("Edit instructions", prefill="Be concise.")
                if text is not None:
                    ctx.runtime.settings_manager.set_custom_instructions(text)
        """
        layout = self._layout()
        if layout is None:
            return None
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str | None] = loop.create_future()

        def _commit(value: str) -> None:
            if not fut.done():
                fut.set_result(value)

        def _cancel() -> None:
            if not fut.done():
                fut.set_result(None)

        layout.open_editor(title, prefill, _commit, _cancel)
        return await fut

    async def custom(
        self,
        factory: Callable[..., Component],
        options: Any = None,
    ) -> Any:
        """Show a fully custom focusable component and wait for it to complete.

        ``factory`` is called with ``(tui, theme, keybindings, done)`` and must
        return a ``Component``.  Call ``done(value)`` from inside the component
        (or from a key handler) to resolve the awaitable and close the overlay.

        ``options.on_handle`` — optional key interceptor that runs before the
        component's own ``handle_input``; return ``True`` to consume the event.

        Usage::

            from tau.tui.tui import CustomOptions, OverlayOptions

            class CounterComponent(Component):
                def __init__(self, done):
                    self._count = 0
                    self._done = done

                def render(self, width):
                    return [f"  Count: {self._count}  (Enter to confirm, Esc to cancel)"]

                def handle_input(self, event):
                    if event.matches("enter"):
                        self._done(self._count)
                        return True
                    if event.matches("escape"):
                        self._done(None)
                        return True
                    if event.matches("up"):
                        self._count += 1
                        return True
                    return False

            result = await ctx.ui.custom(
                lambda tui, theme, kb, done: CounterComponent(done),
                CustomOptions(overlay_options=OverlayOptions(width="40%", anchor="center")),
            )
        """
        layout = self._layout()
        if layout is None:
            return None

        from tau.tui.input import get_keybindings
        from tau.tui.tui import CustomOptions as _CO

        opts = options if options is not None else _CO()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        handle_ref: list[Any] = [None]

        def _done(value: Any) -> None:
            if not fut.done():
                fut.set_result(value)
            if handle_ref[0] is not None:
                handle_ref[0].close()
                handle_ref[0] = None

        component = factory(layout._tui, layout._theme, get_keybindings(), _done)

        if opts.on_handle is not None:
            component = _InterceptComponent(
                component,
                opts.on_handle,  # type: ignore[arg-type]
            )  # type: ignore[assignment]

        overlay_opts = opts.overlay_options
        handle_ref[0] = layout._tui.show_overlay(component, overlay_opts)  # type: ignore[arg-type]
        layout._tui.request_render()

        return await fut

    def show_overlay(
        self,
        component: Component,
        width: int | str = "60%",
        max_height: int | str = "80%",
        anchor: str = "center",
        non_capturing: bool = False,
    ) -> object:
        """Show a floating overlay window and return an OverlayHandle.

        The handle has a ``close()`` method to dismiss the overlay.

        Usage::

            from tau.modes.interactive.components.overlays import TextOverlay

            handle = ctx.ui.show_overlay(
                TextOverlay(["Hello!", "Press Esc to close"]),
                width="50%",
                anchor="top-right",
                non_capturing=True,
            )
            # dismiss later:
            handle.close()
        """
        layout = self._layout()
        if layout is None:
            return _NullHandle()
        from typing import cast as _cast

        from tau.tui.tui import OverlayAnchor, OverlayOptions

        opts = OverlayOptions(
            width=width,
            max_height=max_height,
            anchor=_cast(OverlayAnchor, anchor),
            non_capturing=non_capturing,
        )
        return layout._tui.show_overlay(component, opts)

    def has_active_selector(self) -> bool:
        """True when a capturing overlay or inline selector modal holds keyboard focus.

        Covers both modal mechanisms in the TUI: floating overlays shown via
        ``show_overlay``/``custom`` (e.g. ``ask_user``, ``PromptOverlay``) and
        inline selector modals (``/settings``, ``/model``, ``/resume``,
        ``/theme``, tree, voice picker, etc.), which are handled by a separate
        ``SelectorController`` rather than the overlay stack.

        ``on_terminal_input`` intercept handlers run before either of those get
        a chance to see input, so a global handler (e.g. a push-to-talk key)
        should check this and return ``None``/``False`` when it's true, letting
        the event fall through to whatever modal is open instead of swallowing it.

        Usage::

            def on_key(event):
                if ctx.ui.has_active_selector():
                    return None  # let the open dialog/selector handle it
                ...
        """
        layout = self._layout()
        if layout is None:
            return False
        tui = getattr(layout, "_tui", None)
        if getattr(tui, "_focused_overlay", None) is not None:
            return True
        selectors = getattr(layout, "_selectors", None)
        return bool(getattr(selectors, "is_active", False))

    def notify(self, message: str | list[str], type: str = "info") -> None:  # noqa: A002
        """Show an inline system notification in the message list.

        Pass a str for plain text or a list[str] of pre-rendered lines to get
        the same └ framing used by tool results (apply_render_shell).

        Usage::

            ctx.ui.notify("Done!")
            ctx.ui.notify(["connected", "  pyright  (./)"])
        """
        layout = self._layout()
        if layout is None:
            return
        import time
        from typing import cast

        from tau.message.types import CustomMessage, ImageContent, LinesContent, TextContent

        custom_type = "tool" if type == "tool" else "system"
        _contents: list[TextContent | ImageContent | LinesContent] = (
            [LinesContent(lines=message, notify_type=type)]
            if isinstance(message, list)
            else [TextContent(content=message)]
        )
        msg = CustomMessage(
            custom_type=custom_type,
            timestamp=time.time(),
            contents=cast(list[TextContent | ImageContent | LinesContent], _contents),
        )
        layout.add_message(msg)
        layout._tui.request_render()

    def clear_messages(self) -> None:
        """Clear all messages from the message list."""
        layout = self._layout()
        if layout is None:
            return
        layout.clear_messages()
        layout._tui.request_render()

    # -------------------------------------------------------------------------
    # Header
    # -------------------------------------------------------------------------

    def set_header(
        self,
        factory: Component | Callable[[], Component] | None,
    ) -> None:
        """Inject a component above the message list, or pass None to remove it.

        Usage::

            from tau.tui.component import StaticComponent
            ctx.ui.set_header(StaticComponent(["── My Extension ──"]))
        """
        layout = self._layout()
        if layout is not None:
            layout.set_header(factory)

    # -------------------------------------------------------------------------
    # Terminal title
    # -------------------------------------------------------------------------

    def set_title(self, title: str) -> None:
        """Set the terminal window/tab title.

        Usage::

            ctx.ui.set_title("My Agent – session 42")
        """
        layout = self._layout()
        if layout is not None:
            layout._tui.terminal.set_title(title)

    # -------------------------------------------------------------------------
    # Spinner / working indicator
    # -------------------------------------------------------------------------

    def set_working_message(self, msg: str | None = None) -> None:
        """Override the spinner label while the agent is working.

        Pass None to clear the override and revert to the default label.

        Usage::

            ctx.ui.set_working_message("Fetching results…")
        """
        layout = self._layout()
        if layout is None:
            return
        if msg is None:
            layout.spinner.set_label(layout.spinner._theme.label_thinking)
        else:
            layout.spinner.set_label(msg)
        layout._tui.request_render()

    def set_working_visible(self, visible: bool) -> None:
        """Show or force-hide the working spinner.

        Usage::

            ctx.ui.set_working_visible(False)  # hide spinner entirely
        """
        layout = self._layout()
        if layout is not None:
            layout.spinner.set_force_hidden(not visible)

    def set_working_indicator(
        self,
        frames: list[str] | None = None,
        interval_ms: int | None = None,
    ) -> None:
        """Customise the spinner animation frames and/or speed.

        Pass None for either argument to revert that setting to the theme default.

        Usage::

            ctx.ui.set_working_indicator(["◐", "◓", "◑", "◒"], interval_ms=80)
        """
        layout = self._layout()
        if layout is not None:
            layout.spinner.set_custom_indicator(frames, interval_ms)

    # -------------------------------------------------------------------------
    # Thinking label
    # -------------------------------------------------------------------------

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Change the collapsed thinking-block label, or pass None to reset.

        Usage::

            ctx.ui.set_hidden_thinking_label("reasoning…")
        """
        layout = self._layout()
        if layout is None:
            return
        layout._theme.message.thinking_label = label if label is not None else "thinking…"
        layout._tui.request_render()

    # -------------------------------------------------------------------------
    # Editor content
    # -------------------------------------------------------------------------

    def get_editor_text(self) -> str:
        """Return the current text in the input editor.

        Usage::

            current = ctx.ui.get_editor_text()
        """
        layout = self._layout()
        return layout.get_editor_text() if layout is not None else ""

    def set_editor_text(self, text: str) -> None:
        """Replace the input editor content with the given text.

        Usage::

            ctx.ui.set_editor_text("Summarise the file above")
        """
        layout = self._layout()
        if layout is not None:
            layout.set_editor_text(text)

    def paste_to_editor(self, text: str) -> None:
        """Insert text at the current cursor position in the editor.

        Usage::

            ctx.ui.paste_to_editor("@path/to/file.py ")
        """
        layout = self._layout()
        if layout is not None:
            layout.paste_to_editor(text)

    # -------------------------------------------------------------------------
    # Theme
    # -------------------------------------------------------------------------

    @property
    def theme(self) -> LayoutTheme | None:
        """The active LayoutTheme instance (read-only reference).

        Usage::

            print(ctx.ui.theme.spinner.frames)
        """
        layout = self._layout()
        return layout._theme if layout is not None else None

    def get_all_themes(self) -> list[str]:
        """Return the names of all registered themes.

        Usage::

            names = ctx.ui.get_all_themes()
        """
        from tau.themes.registry import theme_registry

        return theme_registry.list()

    def set_theme(self, theme: str | LayoutTheme, *, persist: bool = False) -> bool:
        """Switch the active theme by name or LayoutTheme instance. Returns True on success.

        Pass a name string to look up a registered theme, or a ``LayoutTheme``
        instance to apply a fully custom theme directly.  Set ``persist=True``
        (only meaningful with a name string) to also save the choice to settings
        so it survives a restart.

        Usage::

            ctx.ui.set_theme("dracula")
            ctx.ui.set_theme("dracula", persist=True)
            ctx.ui.set_theme(LayoutTheme(divider_execute=lambda s: "\\x1b[32m" + s + "\\x1b[0m"))
        """
        from tau.tui.theme import LayoutTheme as _LT

        layout = self._layout()
        if layout is None:
            return False

        if isinstance(theme, str):
            from tau.themes.registry import theme_registry

            try:
                new_theme = theme_registry.get(theme)
            except ValueError:
                return False
            layout.set_theme(new_theme)
            if persist and self._settings is not None:
                self._settings.set_theme(theme)
            return True

        if isinstance(theme, _LT):
            layout.set_theme(theme)
            return True

        return False

    # -------------------------------------------------------------------------
    # Tool calls visibility
    # -------------------------------------------------------------------------

    def get_tools_expanded(self) -> bool:
        """Return whether tool-call blocks are currently shown in the message list.

        Usage::

            expanded = ctx.ui.get_tools_expanded()
        """
        layout = self._layout()
        return layout._theme.message.show_tool_calls if layout is not None else True

    def set_tools_expanded(self, expanded: bool) -> None:
        """Show or hide tool-call blocks in the message list.

        Usage::

            ctx.ui.set_tools_expanded(False)  # collapse all tool calls
        """
        layout = self._layout()
        if layout is not None:
            layout._theme.message.show_tool_calls = expanded
            layout.messages.invalidate()
            layout._tui.request_render()

    def get_tool_results_expanded(self) -> bool:
        """Return whether tool result blocks are currently in expanded mode.

        Usage::

            expanded = ctx.ui.get_tool_results_expanded()
        """
        layout = self._layout()
        if layout is None:
            return False
        if not layout.messages._blocks:
            return False
        return layout.messages._blocks[-1]._expanded

    def set_tool_results_expanded(self, expanded: bool) -> None:
        """Expand or collapse all tool result blocks.

        When expanded, ``render_result`` receives ``opts.expanded=True`` so
        renderers can show full output instead of a one-line preview.

        Usage::

            ctx.ui.set_tool_results_expanded(True)   # show full tool output
            ctx.ui.set_tool_results_expanded(False)  # back to preview
        """
        layout = self._layout()
        if layout is None:
            return
        from tau.message.types import AssistantMessage, ToolMessage

        for block in layout.messages._blocks:
            if isinstance(block.message, (AssistantMessage, ToolMessage)):
                block._expanded = expanded
                block.invalidate()
        layout._tui.request_render()

    # -------------------------------------------------------------------------
    # Raw terminal input subscription
    # -------------------------------------------------------------------------

    def on_terminal_input(self, handler: Callable[[Any], bool | None]) -> Callable[[], None]:
        """Subscribe to raw terminal input events. Returns an unsubscribe callable.

        The handler runs before the editor and any overlays. Return True to
        consume the event — the editor and all other handlers are skipped for
        that keystroke. Return None or False to let it propagate normally.

        Usage::

            def on_key(event):
                if event.matches("ctrl+p"):
                    open_my_panel()
                    return True  # consumed — editor won't see it

            unsub = ctx.ui.on_terminal_input(on_key)
            # later: unsub()
        """
        layout = self._layout()
        if layout is None:
            return lambda: None
        return layout._tui.on_input_intercept(handler)

    # -------------------------------------------------------------------------
    # Render
    # -------------------------------------------------------------------------

    def request_render(self) -> None:
        """Request a TUI re-render — call this after updating a widget's content."""
        layout = self._layout()
        if layout is not None:
            layout._tui.request_render()

    # -------------------------------------------------------------------------
    # Editor input (the prompt text field)
    # -------------------------------------------------------------------------

    def get_input_text(self) -> str:
        """Return the editor's current text (empty string if unavailable)."""
        layout = self._layout()
        if layout is None:
            return ""
        return layout.input.text

    def set_input_text(self, text: str) -> None:
        """Replace the editor's entire buffer and place the cursor at the end."""
        layout = self._layout()
        if layout is None:
            return
        layout.input.set_text(text)
        layout.refresh_input_state()

    def clear_input(self) -> None:
        """Clear the editor's buffer."""
        layout = self._layout()
        if layout is None:
            return
        layout.input.clear()
        layout.refresh_input_state()

    def insert_input_text(self, text: str) -> None:
        """Insert ``text`` at the editor's cursor position and re-render."""
        layout = self._layout()
        if layout is None:
            return
        layout.input.insert_at_cursor(text)
        layout.refresh_input_state()

    def backspace_input(self) -> None:
        """Delete the character immediately before the editor cursor and re-render.

        No-op if the active editor doesn't support backspacing. Lets an extension
        retract a character it inserted at the cursor (e.g. an optimistically
        echoed key) without resetting the cursor the way :meth:`set_input_text`
        would.
        """
        layout = self._layout()
        if layout is None:
            return
        backspace = getattr(layout.input, "backspace", None)
        if backspace is None:
            return
        backspace()
        layout.refresh_input_state()

    def set_input_placeholder(self, text: str) -> None:
        """Override the editor placeholder (shown when the input is empty).

        Call :meth:`reset_input_placeholder` to restore the configured one.
        """
        layout = self._layout()
        if layout is None:
            return
        layout.input.set_placeholder_override(text)
        layout._tui.request_render()

    def reset_input_placeholder(self) -> None:
        """Restore the editor's configured placeholder text."""
        layout = self._layout()
        if layout is None:
            return
        layout.input.set_placeholder_override(None)
        layout._tui.request_render()

    def set_input_cursor(self, renderer: Callable[[str], str]) -> None:
        """Override how the editor's text-cursor cell is drawn.

        ``renderer`` receives the character under the cursor and returns the
        styled cell string (ANSI escapes + glyph). This enables animated or
        coloured carets without reaching into rendering internals. Call
        :meth:`reset_input_cursor` to restore the default block cursor.

        Usage::

            ctx.ui.set_input_cursor(lambda ch: f"\\x1b[38;5;199m█\\x1b[0m")
            # later:
            ctx.ui.reset_input_cursor()
        """
        layout = self._layout()
        if layout is None:
            return
        layout.input.cursor_cell = renderer
        layout._tui.request_render()

    def reset_input_cursor(self) -> None:
        """Restore the editor's default (block) text cursor."""
        from tau.tui.utils import cursor_block

        layout = self._layout()
        if layout is None:
            return
        layout.input.cursor_cell = cursor_block
        layout._tui.request_render()
