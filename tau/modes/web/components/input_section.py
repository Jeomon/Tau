from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.hooks.runtime import InputEventResult
from tau.inference.types import ThinkingLevel
from tau.modes.interactive.input_handler import expand_at_mentions

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


_SUBMIT_ON_ENTER_JS = "(event) => { if (!event.shiftKey) { event.preventDefault(); emit(); } }"

# Up/Down/Tab only ever intercept the keystroke while the autocomplete
# dropdown is actually open (tracked via a data attribute the Python side
# toggles alongside _suggestion_mode — see _render_suggestions/_close_suggestions)
# — otherwise the event passes through untouched, so normal cursor movement
# and tabbing away from the composer are never affected. This is the crux of
# "should not hinder typing": nothing about the autocomplete runs at all
# unless a dropdown is genuinely visible.
_SUGGEST_NAV_JS = (
    "(event) => { if (event.target.dataset.suggestOpen === '1')"
    " { event.preventDefault(); emit(); } }"
)

# Slash commands only trigger when the *entire* message is the command so far
# (matches the TUI's own condition in Layout._sync_pickers: startswith("/")
# and no space yet) — not a "/" appearing mid-sentence.
_SLASH_TRIGGER_RE = re.compile(r"^/(\S*)$")
# @-mentions trigger on an unterminated "@word" at the very end of the text.
# A plain textarea value-change event doesn't expose caret position, so this
# (like pi-web's own trailing-word matching) only completes a mention you're
# actively typing at the end — not one inserted earlier in the message.
_MENTION_TRIGGER_RE = re.compile(r"(?:^|\s)@([^\s@]*)$")

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
}
_MAX_FILE_INDEX = 5000


class InputSection:
    """Prompt input controls for the browser chat page."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._model_button: Any | None = None
        self._model_menu: Any | None = None
        self._model_results: Any | None = None
        self._model_query = ""
        self._effort_button: Any | None = None
        self._effort_menu: Any | None = None
        self._tools_button: Any | None = None
        self._tools_all_enabled = True
        self._compaction_button: Any | None = None
        self._is_compacting = False
        self._sound_button: Any | None = None
        self._sound_enabled = True
        self._send_button: Any | None = None
        self._input_box: Any | None = None
        self._is_running = False
        self._suggestion_menu: Any | None = None
        self._suggestion_results: Any | None = None
        self._suggestion_mode: str | None = None
        self._suggestion_prefix = ""
        self._suggestion_items: list[tuple[str, str]] = []
        self._suggestion_insert_suffix = " "
        self._suggestion_index = 0
        self._file_index: list[str] | None = None
        self._attach_upload: Any | None = None
        self._attachments_row: Any | None = None
        self._pending_attachments: list[dict[str, Any]] = []

    def render(self) -> None:
        """Render the prompt input, send button, and a footer of quick controls."""

        async def send() -> None:
            # Autocomplete is navigate-with-arrows / accept-with-Tab now (see
            # _move_suggestion / _accept_highlighted_suggestion below) — Enter
            # always submits normally and never gets hijacked into accepting
            # a suggestion, so an open dropdown can't block normal typing or
            # sending.
            if self._is_running:
                agent = self._runtime.agent
                if agent is not None:
                    agent.abort()
                self._refresh_send_button()
                return
            value = input_box.value or ""
            if not value.strip() and not self._pending_attachments:
                return
            input_box.value = ""
            options = self._build_prompt_options()
            self._pending_attachments = []
            self._render_attachments()
            self._refresh_send_button()
            try:
                await self._runtime.invoke(value, options)
            except Exception as exc:
                # Runtime.invoke() re-raises when the turn fails outright (e.g.
                # every transient-error retry inside TextLLM is exhausted). If
                # that happens before any content streamed, message_start never
                # fires and nothing tells the transcript the turn is over —
                # the UI just sits there looking hung. Mirrors the TUI's
                # input_handler._invoke, which catches this and surfaces it
                # instead of leaving the spinner running forever.
                ui.notify(f"Error: {exc}", type="negative")

        async def on_agent_error(event: object) -> None:
            ui.notify(f"Error: {getattr(event, 'error', event)}", type="negative")

        # Matches pi-web's own composer wrapper (ChatInput.tsx): capped at
        # 820px and centered, so it doesn't stretch edge-to-edge on wide
        # screens the way the message list above it does.
        with ui.column().classes("w-full max-w-[820px] mx-auto gap-2"):
            attachments_row = ui.row().classes("w-full items-center gap-1 px-2 flex-wrap")
            attachments_row.set_visibility(False)
            self._attachments_row = attachments_row
            # pi-web's composer row itself is alignItems: "center" (ChatInput.tsx),
            # but its send button explicitly overrides that with
            # alignSelf: "flex-end" — pinned to the row's bottom edge so it
            # doesn't drift upward toward vertical-center as the textarea
            # grows for multi-line input. Applying the same to the attach
            # icon keeps both anchored at the bottom in lockstep.
            with ui.row().classes("w-full items-center gap-2 p-2 tau-composer"):
                attach_upload = (
                    ui.upload(
                        multiple=True,
                        auto_upload=True,
                        on_upload=self._on_file_uploaded,
                        max_file_size=25 * 1024 * 1024,
                    )
                    .props(
                        'flat dense hide-upload-btn accept="image/*,audio/*,video/*,'
                        '.pdf,.txt,.md,.json,.py,.js,.ts,.csv,.log"'
                    )
                    .classes("tau-attach-upload self-end")
                )
                attach_upload.props(
                    'title="Attach an image, audio, video, or file (drag onto this to drop)"'
                )
                self._attach_upload = attach_upload

                input_box = (
                    ui.textarea(placeholder="Message Tau...")
                    .props("borderless dense autogrow input-class=py-1")
                    .classes("flex-grow text-[var(--text)] tau-composer-input")
                )
                input_box.on(
                    "keydown.enter",
                    send,
                    js_handler=_SUBMIT_ON_ENTER_JS,
                )
                input_box.on(
                    "keydown.up",
                    lambda: self._move_suggestion(-1),
                    js_handler=_SUGGEST_NAV_JS,
                )
                input_box.on(
                    "keydown.down",
                    lambda: self._move_suggestion(1),
                    js_handler=_SUGGEST_NAV_JS,
                )
                input_box.on(
                    "keydown.tab",
                    self._accept_highlighted_suggestion,
                    js_handler=_SUGGEST_NAV_JS,
                )
                input_box.on_value_change(lambda e: self._on_input_change(e.value or ""))
                # Zero-size floating anchor the suggestion menu targets (see
                # the ui.menu() below) — positioned at the caret's actual
                # pixel location by _position_caret_anchor() each time a
                # suggestion list is shown.
                ui.element("div").props('id="tau-caret-anchor"').style(
                    "position: fixed; width: 0; height: 0; pointer-events: none;"
                )
                self._input_box = input_box
                with (
                    input_box,
                    # no-focus: Quasar's QMenu grabs focus on open by default
                    # (confirmed live — document.activeElement moved to the
                    # menu the instant it opened), which silently swallowed
                    # every Up/Down/Tab keystroke meant for the textarea's
                    # own keydown listeners. Keeping focus on the textarea is
                    # the whole point — the user should never have to click
                    # back into it just to keep typing while a suggestion
                    # list happens to be open.
                    # target="#tau-caret-anchor": anchoring to the textarea
                    # itself (tried first) only ever reaches the input box's
                    # own corner, not the actual "@"/"/" text — confirmed
                    # live, still visibly detached from the cursor whenever
                    # there was other content above or beside it. A plain
                    # <textarea> has no API for caret pixel position, so
                    # _position_caret_anchor() below measures it with the
                    # standard mirror-div technique and moves a floating 0x0
                    # anchor element there each time a suggestion list is
                    # (re)shown; the menu then targets *that* instead of the
                    # textarea, with anchor="top left" self="bottom left" so
                    # it opens directly above the caret and grows upward.
                    ui.menu()
                    .props(
                        'no-parent-event no-focus max-height=280px'
                        ' target="#tau-caret-anchor"'
                        ' anchor="top left" self="bottom left"'
                    )
                    .classes("tau-suggestion-menu") as suggestion_menu,
                ):
                    self._suggestion_results = ui.column().classes("gap-0 py-1.5 min-w-[280px]")
                self._suggestion_menu = suggestion_menu
                self._send_button = (
                    ui.button(on_click=send)
                    .props("unelevated round")
                    .classes("tau-send-button self-end")
                )
                self._refresh_send_button()

            # Matches pi-web's own bottom-bar split (ChatInput.tsx): "LEFT:
            # attach + model selector" vs. "RIGHT: thinking + tools preset +
            # compact + sound", not one flat left-bunched row.
            with ui.row().classes("w-full items-center gap-1 px-1"):
                model_button = (
                    ui.button(self._model_label(), icon="memory")
                    .props("flat no-caps dense")
                    .classes("tau-footer-tab tau-model-tab")
                    .style("color: var(--text-muted) !important;")
                )
                model_button.props(f'title="{self._model_label()}"')
                with (
                    model_button,
                    ui.menu().props("max-height=340px").classes("tau-model-menu") as model_menu,
                ):
                    self._model_menu = model_menu
                    with ui.column().classes("gap-0 min-w-[280px]"):
                        with ui.row().classes(
                            "w-full items-center gap-1.5 px-3 py-2 tau-model-search-wrap"
                        ):
                            ui.icon("search").classes("tau-model-search-icon")
                            model_search = (
                                ui.input(placeholder="Search models...")
                                .props("dense borderless autofocus")
                                .classes("flex-1 text-sm tau-model-search")
                            )
                        model_search.on_value_change(
                            lambda e: self._render_model_results(e.value or "")
                        )
                        self._model_results = ui.column().classes("gap-0 w-full py-1")
                    self._render_model_results("")
                self._model_button = model_button

                with ui.row().classes("items-center gap-1 ml-auto"):
                    effort_button = (
                        ui.button(self._effort_label(), icon="o_lightbulb")
                        .props("flat no-caps dense")
                        .classes("tau-footer-tab")
                        .style("color: var(--text-muted) !important;")
                    )
                    with effort_button, ui.menu().classes("tau-model-menu") as effort_menu:
                        self._effort_menu = effort_menu
                        self._render_effort_menu()
                    self._effort_button = effort_button

                    tools_button = (
                        ui.button(self._tools_label(), icon="build")
                        .props("flat no-caps dense")
                        .classes("tau-footer-tab")
                        .style("color: var(--text-muted) !important;")
                    )
                    tools_button.on("click", lambda _e: self._toggle_tools())
                    tools_button.props(f'title="{self._tools_tooltip()}"')
                    self._tools_button = tools_button

                    compaction_button = (
                        ui.button(self._compaction_label(), icon="compress")
                        .props("flat no-caps dense")
                        .classes("tau-footer-tab")
                        .style("color: var(--text-muted) !important;")
                    )
                    compaction_button.on("click", lambda _e: self._toggle_compaction())
                    compaction_button.props(
                        'title="Summarize the conversation so far to free up context"'
                    )
                    self._compaction_button = compaction_button

                    sound_button = (
                        ui.button(icon=self._sound_icon(), on_click=self._toggle_sound)
                        .props("flat dense round size=sm")
                        .style("color: var(--text-muted) !important;")
                    )
                    sound_button.props(f'title="{self._sound_tooltip()}"')
                    self._sound_button = sound_button

        async def on_model_select(_event: object) -> None:
            self._refresh_model_control()
            self._refresh_effort_control()

        unsub = self._runtime.hooks.register("model_select", on_model_select)

        async def on_input(event: object) -> InputEventResult | None:
            # Matches the TUI's own @-mention behavior (InputHandler._on_submit):
            # the model receives each mentioned file's content, but the
            # displayed chat bubble keeps the short "@path" text as typed —
            # "transform" only replaces what Runtime.invoke() forwards to the
            # agent afterward, not the event object message_list.py already
            # read event.text from to render the bubble.
            text = str(getattr(event, "text", ""))
            expanded = expand_at_mentions(text, self._runtime.session_manager.cwd)
            if expanded == text:
                return None
            return InputEventResult(action="transform", text=expanded)

        input_unsub = self._runtime.hooks.register("input", on_input)

        async def on_agent_start(_event: object) -> None:
            self._is_running = True
            self._refresh_send_button()

        async def on_agent_end(_event: object) -> None:
            self._is_running = False
            self._refresh_send_button()
            if self._sound_enabled:
                self._play_done_sound()

        agent_start_unsub = self._runtime.hooks.register("agent_start", on_agent_start)
        agent_end_unsub = self._runtime.hooks.register("agent_end", on_agent_end)
        agent_error_unsub = self._runtime.hooks.register("agent_error", on_agent_error)

        async def on_compaction_start(_event: object) -> None:
            self._is_compacting = True
            self._refresh_compaction_control()

        async def on_compaction_end(_event: object) -> None:
            self._is_compacting = False
            self._refresh_compaction_control()
            ui.notify("Compaction completed.", type="positive")

        async def on_compaction_failure(event: object) -> None:
            self._is_compacting = False
            self._refresh_compaction_control()
            ui.notify(f"Compaction failed: {getattr(event, 'error', '')}", type="negative")

        async def on_compaction_cancelled(_event: object) -> None:
            self._is_compacting = False
            self._refresh_compaction_control()
            ui.notify("Compaction cancelled.", type="warning")

        compaction_unsubs = [
            self._runtime.hooks.register("compaction_start", on_compaction_start),
            self._runtime.hooks.register("compaction_end", on_compaction_end),
            self._runtime.hooks.register("compaction_failure", on_compaction_failure),
            self._runtime.hooks.register("compaction_cancelled", on_compaction_cancelled),
        ]

        ui.context.client.on_disconnect(
            lambda: [
                unsub(),
                input_unsub(),
                agent_start_unsub(),
                agent_end_unsub(),
                agent_error_unsub(),
                *(u() for u in compaction_unsubs),
            ]
        )

    def _has_prompt_text(self) -> bool:
        value = getattr(self._input_box, "value", None)
        return bool(value and str(value).strip())

    def _refresh_send_button(self) -> None:
        if self._send_button is None:
            return
        if self._is_running:
            self._send_button.props("icon=stop")
            self._send_button.enable()
            self._send_button.classes(remove="tau-send-button-idle tau-send-button-disabled")
            self._send_button.classes(add="tau-send-button-running")
            return

        self._send_button.props("icon=arrow_upward")
        self._send_button.classes(remove="tau-send-button-running")
        # classes(add=...) never removed the *other* state's class, so once
        # both idle and disabled had occurred at least once, both classes
        # stuck around simultaneously — always remove the opposite one.
        if self._has_prompt_text():
            self._send_button.enable()
            self._send_button.classes(remove="tau-send-button-disabled", add="tau-send-button-idle")
        else:
            self._send_button.disable()
            self._send_button.classes(remove="tau-send-button-idle", add="tau-send-button-disabled")

    def _current_model(self) -> Any | None:
        agent = self._runtime.agent
        if agent is None:
            return None
        return getattr(agent._engine.llm, "model", None)

    def _model_label(self) -> str:
        model = self._current_model()
        if model is None:
            return "Model"
        return str(getattr(model, "name", None) or getattr(model, "id", "Model"))

    def _available_models(self) -> list[Any]:
        from tau.inference.api.text.service import TextLLM

        try:
            return TextLLM.list_available()
        except Exception:
            return []

    def _render_model_results(self, query: str) -> None:
        """(Re)populate the filtered model list without touching the search
        box itself — it lives in a sibling container so retyping doesn't
        rebuild (and steal focus from) the input on every keystroke."""
        self._model_query = query
        if self._model_results is None:
            return
        self._model_results.clear()
        current = self._current_model()
        current_key = (
            f"{getattr(current, 'provider', '')}/{getattr(current, 'id', '')}"
            if current is not None
            else None
        )
        needle = query.strip().lower()
        models = [
            m
            for m in self._available_models()
            if not needle
            or needle in m.id.lower()
            or needle in m.name.lower()
            or needle in m.provider.lower()
        ]
        models.sort(key=lambda m: (m.provider, m.name.lower()))
        with self._model_results:
            if not models:
                ui.menu_item("No matching models").props("disable")
                return
            last_provider: str | None = None
            for model in models:
                if model.provider != last_provider:
                    if last_provider is not None:
                        ui.separator().classes("my-1")
                    ui.label(model.provider).classes(
                        "px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide"
                        " text-[var(--text-dim)]"
                    )
                    last_provider = model.provider
                is_current = f"{model.provider}/{model.id}" == current_key
                item = (
                    ui.menu_item(on_click=lambda _event, m=model: self._set_model(m))
                    .props("clickable")
                    .classes("tau-model-item" + (" tau-model-item-active" if is_current else ""))
                )
                with item:
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.label(model.name).classes("flex-1 text-sm")
                        if is_current:
                            ui.icon("check").classes("tau-model-item-check")

    def _refresh_model_control(self) -> None:
        self._render_model_results(self._model_query)
        if self._model_button is not None:
            # Set via the props dict directly, not the `.props("k=v ...")`
            # string form — that form splits on whitespace, so a multi-word
            # value like "Claude Haiku 4.5" would truncate to "Claude".
            label = self._model_label()
            self._model_button.props["label"] = label
            self._model_button.props["title"] = label

    async def _set_model(self, model: Any) -> None:
        if self._is_running:
            ui.notify("Wait for the agent to finish before switching models", type="warning")
            return
        ok = await self._runtime.set_model(model.id, model.provider)
        if ok:
            ui.notify(f"Model set to {model.name}", type="positive")
        else:
            ui.notify(f"Could not switch to {model.name}", type="negative")
        self._refresh_model_control()
        self._refresh_effort_control()

    def _available_effort_levels(self) -> list[ThinkingLevel]:
        agent = self._runtime.agent
        model = getattr(agent._engine.llm, "model", None) if agent is not None else None
        if model is None or not getattr(model, "thinking", False):
            return [ThinkingLevel.Off]
        return list(getattr(model, "thinking_levels", None) or list(ThinkingLevel))

    def _render_effort_menu(self) -> None:
        if self._effort_menu is None:
            return
        self._effort_menu.clear()
        current = self._effort_label()
        with self._effort_menu:
            with ui.column().classes("gap-0 min-w-[160px] py-1"):
                for level in self._available_effort_levels():
                    is_current = level.value == current
                    item = (
                        ui.menu_item(on_click=lambda _event, lv=level: self._set_effort(lv))
                        .classes("tau-model-item" + (" tau-model-item-active" if is_current else ""))
                    )
                    with item, ui.row().classes("w-full items-center gap-2"):
                        ui.label(level.value).classes("flex-1 text-sm")
                        if is_current:
                            ui.icon("check").classes("tau-model-item-check")

    def _refresh_effort_control(self) -> None:
        self._render_effort_menu()
        if self._effort_button is not None:
            self._effort_button.props(f"label={self._effort_label()}")

    def _effort_label(self) -> str:
        llm = self._runtime.agent._engine.llm if self._runtime.agent is not None else None
        model = getattr(llm, "model", None) if llm is not None else None
        if model is None or not getattr(model, "thinking", False):
            return ThinkingLevel.Off.value
        opts = getattr(getattr(llm, "api", None), "options", None) if llm is not None else None
        level = getattr(opts, "thinking_level", None) if opts is not None else None
        return level.value if level is not None else ThinkingLevel.Off.value

    def _tools_label(self) -> str:
        return "Tools: All" if self._tools_all_enabled else "Tools: Off"

    def _tools_tooltip(self) -> str:
        return (
            "All tools available to the agent — click to disable tools"
            if self._tools_all_enabled
            else "Tools disabled — click to re-enable all tools"
        )

    def _toggle_tools(self) -> None:
        agent = self._runtime.agent
        if agent is None:
            return
        # Mirrors ExtensionAPI.set_active_tools (tau/extensions/api.py), inlined
        # since that API is meant for extensions holding a registration-time
        # handle, not a one-off call from here — and its "empty list" convention
        # means "no restriction", so it can't itself express "zero tools".
        registry = getattr(getattr(self._runtime, "_context", None), "tool_registry", None)
        if registry is None:
            return
        self._tools_all_enabled = not self._tools_all_enabled
        agent._engine.tools = list(registry.list()) if self._tools_all_enabled else []
        if self._tools_button is not None:
            self._tools_button.props["label"] = self._tools_label()
            self._tools_button.props["title"] = self._tools_tooltip()
        ui.notify(
            "All tools enabled" if self._tools_all_enabled else "Tools disabled for this session",
            type="positive" if self._tools_all_enabled else "warning",
        )

    def _compaction_label(self) -> str:
        return "Stop" if self._is_compacting else "Compact"

    def _refresh_compaction_control(self) -> None:
        if self._compaction_button is not None:
            self._compaction_button.props["label"] = self._compaction_label()

    async def _toggle_compaction(self) -> None:
        agent = self._runtime.agent
        if agent is None:
            return
        if self._is_compacting:
            agent.abort()
            return
        try:
            did_compact = await agent.compact()
        except Exception as e:
            ui.notify(f"Compaction failed: {e}", type="negative")
            return
        if not did_compact:
            ui.notify("Nothing to compact — conversation is too short to summarize.", type="warning")

    def _sound_icon(self) -> str:
        return "volume_up" if self._sound_enabled else "volume_off"

    def _sound_tooltip(self) -> str:
        return "Disable completion sound" if self._sound_enabled else "Enable completion sound"

    def _toggle_sound(self) -> None:
        self._sound_enabled = not self._sound_enabled
        if self._sound_button is not None:
            self._sound_button.props["icon"] = self._sound_icon()
            self._sound_button.props["title"] = self._sound_tooltip()
        if self._sound_enabled:
            self._play_done_sound()

    def _play_done_sound(self) -> None:
        """Two-tone completion chime via WebAudio — no audio file needed.

        Ported from pi-web's useAudio.ts (same two notes, same envelope).
        Uses an element's bound client rather than the top-level
        ui.run_javascript(), which needs page context this hook callback
        doesn't reliably have.
        """
        if self._send_button is None:
            return
        self._send_button.client.run_javascript(
            """
            (function() {
                try {
                    window.__tauAudioCtx = window.__tauAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
                    var ctx = window.__tauAudioCtx;
                    var play = function() {
                        var now = ctx.currentTime;
                        [523.25, 659.25].forEach(function(freq, i) {
                            var osc = ctx.createOscillator();
                            var gain = ctx.createGain();
                            osc.connect(gain);
                            gain.connect(ctx.destination);
                            osc.type = 'sine';
                            osc.frequency.value = freq;
                            var t = now + i * 0.18;
                            gain.gain.setValueAtTime(0, t);
                            gain.gain.linearRampToValueAtTime(0.18, t + 0.02);
                            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.45);
                            osc.start(t);
                            osc.stop(t + 0.45);
                        });
                    };
                    if (ctx.state === 'suspended') { ctx.resume().then(play).catch(function() {}); }
                    else { play(); }
                } catch (e) { /* AudioContext unavailable */ }
            })();
            """
        )

    # -- Slash-command / @-mention autocomplete ------------------------------

    def _on_input_change(self, text: str) -> None:
        self._refresh_send_button()
        slash_match = _SLASH_TRIGGER_RE.match(text)
        if slash_match:
            self._show_command_suggestions(slash_match.group(1))
            return
        mention_match = _MENTION_TRIGGER_RE.search(text)
        if mention_match:
            self._show_mention_suggestions(text, mention_match)
            return
        self._close_suggestions()

    def _close_suggestions(self) -> None:
        self._suggestion_mode = None
        self._suggestion_items = []
        self._suggestion_index = 0
        if self._suggestion_menu is not None:
            self._suggestion_menu.close()
        self._set_suggest_open_flag(False)

    def _set_suggest_open_flag(self, open_: bool) -> None:
        """Toggle the data attribute _SUGGEST_NAV_JS checks — the single
        source of truth for whether Up/Down/Tab should intercept the
        keystroke at all, kept in sync with _suggestion_mode.

        getHtmlElement(id) resolves directly to ui.textarea's own <textarea>
        node (confirmed live — unlike ui.button, where the id lands on an
        outer wrapper), which is also what event.target is on keydown, so
        no extra traversal is needed to reach the element the JS-side check
        in _SUGGEST_NAV_JS reads from.
        """
        if self._input_box is None:
            return
        self._input_box.client.run_javascript(
            f"""
            (function() {{
                var el = getHtmlElement({self._input_box.id});
                if (el) {{ el.dataset.suggestOpen = '{"1" if open_ else "0"}'; }}
            }})();
            """
        )

    def _show_command_suggestions(self, query: str) -> None:
        from tau.prompts.registry import prompt_registry
        from tau.tui.utils import fuzzy_filter

        self._suggestion_mode = "command"
        self._suggestion_prefix = ""
        entries: list[tuple[str, str]] = [
            (c.name, c.description) for c in self._runtime.commands.list()
        ]
        entries += [(p.name, p.description or "") for p in prompt_registry.list()]
        entries.sort(key=lambda e: e[0])
        filtered = fuzzy_filter(entries, query, lambda e: f"{e[0]} {e[1]}") if query else entries
        self._render_suggestions(
            [(f"/{name}", description) for name, description in filtered[:20]],
            insert_suffix=" ",
        )

    def _show_mention_suggestions(self, text: str, match: re.Match[str]) -> None:
        from tau.tui.utils import fuzzy_filter

        self._suggestion_mode = "mention"
        self._suggestion_prefix = text[: match.start(1)]
        query = match.group(1)
        candidates = self._search_files()
        filtered = fuzzy_filter(candidates, query, lambda p: p) if query else candidates
        # `_suggestion_prefix` already ends right after the "@" (see
        # match.start(1) above), so the inserted value must NOT repeat it.
        self._render_suggestions(
            [(path, "") for path in filtered[:20]],
            insert_suffix=" ",
        )

    def _search_files(self) -> list[str]:
        """Lazily build (and cache for the session) a flat relative-path index
        under the session's cwd, mirroring file_explorer.py's tree walk but
        flattened for fuzzy filename matching instead of a tree widget."""
        if self._file_index is not None:
            return self._file_index
        sm = self._runtime.session_manager
        root = sm.cwd if sm is not None else None
        if root is None:
            self._file_index = []
            return self._file_index
        paths: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if len(paths) >= _MAX_FILE_INDEX:
                    break
                full = os.path.join(dirpath, name)
                paths.append(os.path.relpath(full, root))
            if len(paths) >= _MAX_FILE_INDEX:
                break
        paths.sort()
        self._file_index = paths
        return paths

    def _render_suggestions(self, items: list[tuple[str, str]], *, insert_suffix: str) -> None:
        if self._suggestion_results is None or self._suggestion_menu is None:
            return
        self._suggestion_items = items
        self._suggestion_insert_suffix = insert_suffix
        self._suggestion_index = 0
        self._set_suggest_open_flag(bool(items))
        self._render_suggestion_rows()
        self._position_caret_anchor()
        self._suggestion_menu.open()

    def _position_caret_anchor(self) -> None:
        """Move #tau-caret-anchor to the textarea's actual caret position
        (mirror-div technique — a plain <textarea> has no native API for
        this) so the menu, targeting that anchor, tracks where the "@"/"/"
        text really is instead of just some corner of the whole input box.
        Called every time the suggestion list is (re)filtered, since typing
        more characters keeps moving the caret while the dropdown is open.
        """
        if self._input_box is None:
            return
        self._input_box.client.run_javascript(
            f"""
            (function() {{
                var ta = getHtmlElement({self._input_box.id});
                var anchor = document.getElementById('tau-caret-anchor');
                if (!ta || !anchor) return;
                var style = getComputedStyle(ta);
                var mirror = document.createElement('div');
                var props = ['boxSizing', 'width', 'paddingTop', 'paddingRight',
                    'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderRightWidth',
                    'borderBottomWidth', 'borderLeftWidth', 'fontStyle', 'fontVariant',
                    'fontWeight', 'fontSize', 'lineHeight', 'fontFamily', 'letterSpacing',
                    'wordSpacing', 'tabSize'];
                props.forEach(function(p) {{ mirror.style[p] = style[p]; }});
                mirror.style.position = 'absolute';
                mirror.style.visibility = 'hidden';
                mirror.style.whiteSpace = 'pre-wrap';
                mirror.style.wordWrap = 'break-word';
                mirror.style.top = '0';
                mirror.style.left = '-9999px';
                document.body.appendChild(mirror);
                var caret = ta.selectionEnd;
                mirror.textContent = ta.value.substring(0, caret);
                var marker = document.createElement('span');
                marker.textContent = '\\u200b';
                mirror.appendChild(marker);
                var taRect = ta.getBoundingClientRect();
                var lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
                var top = taRect.top + (marker.offsetTop - ta.scrollTop);
                var left = taRect.left + (marker.offsetLeft - ta.scrollLeft);
                document.body.removeChild(mirror);
                anchor.style.top = top + 'px';
                anchor.style.left = left + 'px';
                anchor.style.height = lineHeight + 'px';
            }})();
            """
        )

    def _render_suggestion_rows(self) -> None:
        """(Re)draw the dropdown's rows, highlighting whichever one
        _suggestion_index currently points at — called both on a fresh
        filter result and after Up/Down moves the highlight."""
        if self._suggestion_results is None:
            return
        self._suggestion_results.clear()
        with self._suggestion_results:
            if not self._suggestion_items:
                ui.menu_item("No matches").props("disable")
                return
            for i, (value, description) in enumerate(self._suggestion_items):
                label = f"{value}  {description}" if description else value
                classes = "text-xs tau-suggestion-item"
                if i == self._suggestion_index:
                    classes += " tau-suggestion-active"
                ui.menu_item(
                    label,
                    on_click=lambda v=value: self._select_suggestion(v),
                ).classes(classes)

    def _move_suggestion(self, delta: int) -> None:
        """Up/Down: move the highlight, wrapping around at either end."""
        if self._suggestion_mode is None or not self._suggestion_items:
            return
        count = len(self._suggestion_items)
        self._suggestion_index = (self._suggestion_index + delta) % count
        self._render_suggestion_rows()

    def _accept_highlighted_suggestion(self) -> None:
        """Tab: insert whichever row is currently highlighted."""
        if self._suggestion_mode is None or not self._suggestion_items:
            self._close_suggestions()
            return
        value, _description = self._suggestion_items[self._suggestion_index]
        self._select_suggestion(value)

    def _select_suggestion(self, value: str) -> None:
        if self._input_box is not None:
            self._input_box.value = self._suggestion_prefix + value + self._suggestion_insert_suffix
            self._input_box.run_method("focus")
        self._close_suggestions()
        self._refresh_send_button()

    # -- Attachments -----------------------------------------------------------

    @staticmethod
    def _classify_attachment(content_type: str) -> str:
        if content_type.startswith("image/"):
            return "image"
        if content_type.startswith("audio/"):
            return "audio"
        if content_type.startswith("video/"):
            return "video"
        return "file"

    async def _on_file_uploaded(self, event: Any) -> None:
        file = event.file
        data = await file.read()
        kind = self._classify_attachment(file.content_type or "")
        self._pending_attachments.append({"kind": kind, "name": file.name, "data": data})
        self._render_attachments()
        if self._attach_upload is not None:
            self._attach_upload.reset()

    def _remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._pending_attachments):
            self._pending_attachments.pop(index)
        self._render_attachments()

    def _render_attachments(self) -> None:
        if self._attachments_row is None:
            return
        self._attachments_row.clear()
        self._attachments_row.set_visibility(bool(self._pending_attachments))
        icons = {"image": "image", "audio": "audiotrack", "video": "videocam", "file": "description"}
        with self._attachments_row:
            for index, attachment in enumerate(self._pending_attachments):
                with ui.row().classes("items-center gap-1 px-2 py-1 tau-attachment-chip"):
                    ui.icon(icons[attachment["kind"]]).classes("text-[var(--text-dim)]").style(
                        "font-size: 14px;"
                    )
                    ui.label(attachment["name"]).classes(
                        "text-xs text-[var(--text)] truncate max-w-[140px]"
                    )
                    close_icon = ui.icon("close").classes(
                        "text-xs cursor-pointer text-[var(--text-dim)]"
                    )
                    close_icon.on("click", lambda _e, i=index: self._remove_attachment(i))

    def _build_prompt_options(self) -> Any | None:
        if not self._pending_attachments:
            return None
        from tau.agent.types import PromptOptions

        by_kind: dict[str, list[bytes]] = {"image": [], "audio": [], "video": [], "file": []}
        for attachment in self._pending_attachments:
            by_kind[attachment["kind"]].append(attachment["data"])
        return PromptOptions(
            images=by_kind["image"],
            audio=by_kind["audio"],
            video=by_kind["video"],
            file=by_kind["file"],
        )

    async def _set_effort(self, level: ThinkingLevel) -> None:
        from tau.hooks.tui import ThinkingLevelSelectEvent

        agent = self._runtime.agent
        if agent is None:
            return
        llm = agent._engine.llm
        previous_level = llm.api.options.thinking_level
        level = llm.model.clamp_thinking_level(level) or llm.model.default_thinking_level or level
        llm.api.options.thinking_level = None if level == ThinkingLevel.Off else level

        sm = self._runtime.session_manager
        if sm is not None:
            sm.append_thinking_level_change(level)

        settings = self._runtime.settings_manager
        if settings is not None:
            settings.set_thinking_level(level)

        await self._runtime.hooks.emit(
            ThinkingLevelSelectEvent(level=level, previous_level=previous_level)
        )

        self._refresh_effort_control()
        ui.notify(f"Effort set to {level.value}", type="positive")
