from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


class InputSection:
    """Prompt input controls for the browser chat page."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    def render(self) -> None:
        """Render the prompt input and send button."""

        async def send() -> None:
            value = input_box.value
            if not value or not value.strip():
                return
            input_box.value = ""
            await self._runtime.invoke(value)

        with ui.row().classes("w-full items-end gap-2 p-2.5 pl-4 tau-composer"):
            input_box = (
                ui.textarea(placeholder="Message Tau...")
                .props("borderless dense autogrow input-class=py-1")
                .classes("flex-grow text-[var(--text)]")
                .style("max-height: 200px")
            )
            input_box.on("keydown.enter.prevent", send)
            ui.button(on_click=send).props("unelevated icon=arrow_upward round").style(
                "background: var(--accent) !important; color: #fff !important;"
                " box-shadow: 0 1px 3px rgba(37, 99, 235, 0.25) !important;"
            ).bind_enabled_from(input_box, "value", backward=lambda v: bool(v and v.strip()))
