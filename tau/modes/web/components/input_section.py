from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.inference.types import ThinkingLevel

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


_SUBMIT_ON_ENTER_JS = "(event) => { if (!event.shiftKey) { event.preventDefault(); emit(); } }"


class InputSection:
    """Prompt input controls for the browser chat page."""

    def __init__(
        self, runtime: Runtime, *, on_toggle_compact: Callable[[bool], None] | None = None
    ) -> None:
        self._runtime = runtime
        self._on_toggle_compact = on_toggle_compact
        self._compact = False
        self._model_button: Any | None = None
        self._model_menu: Any | None = None
        self._model_results: Any | None = None
        self._model_query = ""
        self._effort_button: Any | None = None
        self._effort_menu: Any | None = None
        self._compact_button: Any | None = None
        self._send_button: Any | None = None
        self._input_box: Any | None = None
        self._is_running = False

    def render(self) -> None:
        """Render the prompt input, send button, and a footer of quick controls."""

        async def send() -> None:
            if self._is_running:
                agent = self._runtime.agent
                if agent is not None:
                    agent.abort()
                self._refresh_send_button()
                return
            value = input_box.value
            if not value or not value.strip():
                return
            input_box.value = ""
            self._refresh_send_button()
            await self._runtime.invoke(value)

        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("w-full items-end gap-2 p-2.5 pl-4 tau-composer"):
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
                input_box.on_value_change(lambda _event: self._refresh_send_button())
                self._input_box = input_box
                self._send_button = (
                    ui.button(on_click=send).props("unelevated round").classes("tau-send-button")
                )
                self._refresh_send_button()

            with ui.row().classes("items-center gap-1 px-1"):
                model_button = (
                    ui.button(self._model_label(), icon="memory")
                    .props("flat no-caps dense")
                    .classes("tau-footer-tab tau-model-tab")
                    .style("color: var(--text-muted) !important;")
                )
                model_button.props(f'title="{self._model_label()}"')
                with model_button, ui.menu().props("max-height=340px") as model_menu:
                    self._model_menu = model_menu
                    with ui.column().classes("gap-0 min-w-[260px]"):
                        model_search = (
                            ui.input(placeholder="Search models...")
                            .props("dense borderless")
                            .classes("px-3 pt-2 pb-1 text-xs")
                        )
                        model_search.on_value_change(
                            lambda e: self._render_model_results(e.value or "")
                        )
                        self._model_results = ui.column().classes("gap-0 w-full")
                    self._render_model_results("")
                self._model_button = model_button

                effort_button = (
                    ui.button(self._effort_label(), icon="lightbulb")
                    .props("flat no-caps dense")
                    .classes("tau-footer-tab")
                    .style("color: var(--text-muted) !important;")
                )
                with effort_button, ui.menu() as effort_menu:
                    self._effort_menu = effort_menu
                    self._render_effort_menu()
                self._effort_button = effort_button

                compact_button = (
                    ui.button(icon=self._compact_icon(), on_click=self._toggle_compact)
                    .props("flat dense round size=sm")
                    .classes("ml-2")
                    .style("color: var(--text-muted) !important;")
                )
                compact_button.tooltip(self._compact_tooltip())
                self._compact_button = compact_button

        async def on_model_select(_event: object) -> None:
            self._refresh_model_control()
            self._refresh_effort_control()

        unsub = self._runtime.hooks.register("model_select", on_model_select)

        async def on_agent_start(_event: object) -> None:
            self._is_running = True
            self._refresh_send_button()

        async def on_agent_end(_event: object) -> None:
            self._is_running = False
            self._refresh_send_button()

        agent_start_unsub = self._runtime.hooks.register("agent_start", on_agent_start)
        agent_end_unsub = self._runtime.hooks.register("agent_end", on_agent_end)
        ui.context.client.on_disconnect(lambda: [unsub(), agent_start_unsub(), agent_end_unsub()])

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
        if self._has_prompt_text():
            self._send_button.enable()
        else:
            self._send_button.disable()
        self._send_button.classes(remove="tau-send-button-running")
        self._send_button.classes(
            add="tau-send-button-idle" if self._has_prompt_text() else "tau-send-button-disabled"
        )

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
                        ui.separator()
                    ui.label(model.provider).classes(
                        "px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-[var(--text-dim)]"
                    )
                    last_provider = model.provider
                is_current = f"{model.provider}/{model.id}" == current_key
                label = f"{'✓ ' if is_current else ''}{model.name}"
                ui.menu_item(label, on_click=lambda _event, m=model: self._set_model(m))

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
        with self._effort_menu:
            for level in self._available_effort_levels():
                ui.menu_item(level.value, on_click=lambda _event, lv=level: self._set_effort(lv))

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

    def _compact_icon(self) -> str:
        return "unfold_more" if self._compact else "unfold_less"

    def _compact_tooltip(self) -> str:
        return "Expand message spacing" if self._compact else "Compact message spacing"

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

    def _toggle_compact(self) -> None:
        self._compact = not self._compact
        if self._compact_button is not None:
            self._compact_button.props(f"icon={self._compact_icon()}")
        if self._on_toggle_compact is not None:
            self._on_toggle_compact(self._compact)
