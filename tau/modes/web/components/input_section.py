from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.inference.types import ThinkingLevel

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


class InputSection:
    """Prompt input controls for the browser chat page."""

    def __init__(self, runtime: Runtime, *, on_toggle_compact: Callable[[bool], None] | None = None) -> None:
        self._runtime = runtime
        self._on_toggle_compact = on_toggle_compact
        self._compact = False
        self._effort_button: Any | None = None
        self._compact_button: Any | None = None

    def render(self) -> None:
        """Render the prompt input, send button, and a footer of quick controls."""

        async def send() -> None:
            value = input_box.value
            if not value or not value.strip():
                return
            input_box.value = ""
            await self._runtime.invoke(value)

        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("w-full items-end gap-2 p-2.5 pl-4 tau-composer"):
                input_box = (
                    ui.textarea(placeholder="Message Tau...")
                    .props("borderless dense autogrow input-class=py-1")
                    .classes("flex-grow text-[var(--text)]")
                    .style("max-height: 200px")
                )
                input_box.on("keydown.enter.exact.prevent", send)
                ui.button(on_click=send).props("unelevated icon=arrow_upward round").style(
                    "background: var(--accent) !important; color: #fff !important;"
                    " box-shadow: 0 1px 3px rgba(37, 99, 235, 0.25) !important;"
                ).bind_enabled_from(input_box, "value", backward=lambda v: bool(v and v.strip()))

            with ui.row().classes("items-center gap-1 px-1"):
                effort_button = (
                    ui.button(self._effort_label(), icon="tune")
                    .props("flat no-caps dense")
                    .classes("tau-footer-tab")
                    .style("color: var(--text-muted) !important;")
                )
                with effort_button, ui.menu():
                    for level in ThinkingLevel:
                        ui.menu_item(level.value, on_click=lambda lv=level: self._set_effort(lv))
                self._effort_button = effort_button

                compact_button = (
                    ui.button(icon=self._compact_icon(), on_click=self._toggle_compact)
                    .props("flat dense round size=sm")
                    .classes("ml-2")
                    .style("color: var(--text-muted) !important;")
                )
                compact_button.tooltip(self._compact_tooltip())
                self._compact_button = compact_button

    def _effort_label(self) -> str:
        llm = self._runtime.agent._engine.llm if self._runtime.agent is not None else None
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

        if self._effort_button is not None:
            self._effort_button.props(f"label={self._effort_label()}")
        ui.notify(f"Effort set to {level.value}", type="positive")

    def _toggle_compact(self) -> None:
        self._compact = not self._compact
        if self._compact_button is not None:
            self._compact_button.props(f"icon={self._compact_icon()}")
        if self._on_toggle_compact is not None:
            self._on_toggle_compact(self._compact)
