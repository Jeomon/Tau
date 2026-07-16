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

        with (
            ui.column().classes("w-full h-1/6 min-h-0 justify-end"),
            ui.row().classes("w-full items-center"),
        ):
            input_box = ui.input(placeholder="Message Tau...").classes("flex-grow")
            input_box.on("keydown.enter", send)
            ui.button("Send", on_click=send)
