from __future__ import annotations

from typing import TYPE_CHECKING

import click
import uvicorn
from fastapi import FastAPI
from nicegui import ui

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

# Hook events that get echoed into the page log. Mirrors the subset used by
# `_run_json` in tau/console/cli.py; kept minimal here since this is a first
# scaffold, not the final presentation layer.
_HOOK_NAMES = (
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_end",
    "settled",
)


def _describe(event: object) -> str:
    """Render a hook event as one log line."""
    message = getattr(event, "message", None)
    text = getattr(message, "text_content", None)
    if callable(text):
        return f"{type(event).__name__}: {text()}"
    return type(event).__name__


class App:
    """NiceGUI browser app for one Tau runtime."""

    def __init__(self, runtime: Runtime, host: str = "127.0.0.1", port: int = 8080) -> None:
        self._runtime = runtime
        self._host = host
        self._port = port
        self._fastapi_app = FastAPI()

    @classmethod
    async def create(cls, runtime: Runtime, host: str = "127.0.0.1", port: int = 8080) -> App:
        """Build the browser app around an already-constructed Runtime."""
        return cls(runtime, host=host, port=port)

    async def run(self) -> None:
        """Run the browser app until the server stops.

        Mounted onto our own FastAPI/uvicorn server (via `ui.run_with`) rather than
        `ui.run()`, so it shares the event loop `Runtime` already runs on instead
        of spinning up a second one.
        """
        self._register_pages()
        ui.run_with(self._fastapi_app, title="Tau", storage_secret="tau-web")

        config = uvicorn.Config(
            self._fastapi_app,
            host=self._host,
            port=self._port,
            loop="asyncio",
            log_level="warning",
        )
        server = uvicorn.Server(config)
        click.echo(f"Tau web UI serving at http://{self._host}:{self._port}")
        await server.serve()

    def _register_pages(self) -> None:
        @ui.page("/")
        def index() -> None:
            self._render_index()

    def _render_index(self) -> None:
        ui.label("Tau").classes("text-2xl font-bold")
        log = ui.log().classes("w-full h-96")

        async def on_event(event: object) -> None:
            log.push(_describe(event))

        unsubs = [self._runtime.hooks.register(name, on_event) for name in _HOOK_NAMES]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])

        async def send() -> None:
            value = input_box.value
            if not value or not value.strip():
                return
            input_box.value = ""
            await self._runtime.invoke(value)

        with ui.row().classes("w-full items-center"):
            input_box = ui.input(placeholder="Message Tau...").classes("flex-grow")
            input_box.on("keydown.enter", send)
            ui.button("Send", on_click=send)


async def run_web(runtime: Runtime, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run Tau's browser-based app using NiceGUI."""
    app = await App.create(runtime, host=host, port=port)
    await app.run()
