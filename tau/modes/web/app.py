from __future__ import annotations

from typing import TYPE_CHECKING

import click
import uvicorn
from fastapi import FastAPI
from nicegui import ui

from tau.modes.web import theme
from tau.modes.web.pages.chat import ChatPage

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


class App:
    """NiceGUI browser app for one Tau runtime."""

    def __init__(self, runtime: Runtime, host: str = "127.0.0.1", port: int = 8079) -> None:
        self._runtime = runtime
        self._host = host
        self._port = port
        self._fastapi_app = FastAPI()

    @classmethod
    async def create(cls, runtime: Runtime, host: str = "127.0.0.1", port: int = 8079) -> App:
        """Build the browser app around an already-constructed Runtime."""
        return cls(runtime, host=host, port=port)

    async def run(self) -> None:
        """Run the browser app until the server stops.

        Mounted onto our own FastAPI/uvicorn server (via `ui.run_with`) rather than
        `ui.run()`, so it shares the event loop `Runtime` already runs on instead
        of spinning up a second one.
        """
        self._register_pages()
        ui.add_css(theme.CSS, shared=True)
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
            ChatPage(self._runtime).render()


async def run_web(runtime: Runtime, host: str = "127.0.0.1", port: int = 8079) -> None:
    """Run Tau's browser-based app using NiceGUI."""
    app = await App.create(runtime, host=host, port=port)
    await app.run()
