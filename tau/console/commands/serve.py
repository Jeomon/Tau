from __future__ import annotations

import asyncio
from pathlib import Path

import click


@click.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind the web server to.")
@click.option("--port", "-p", default=8080, show_default=True, type=int, help="Port to bind the web server to.")
@click.option("--provider", default=None, help="Provider to use (e.g. groq, mistral, openrouter).")
@click.option(
    "--model",
    default=None,
    help="Model ID, or provider/model shorthand (e.g. groq/llama-3.3-70b-versatile).",
)
def serve(host: str, port: int, provider: str | None, model: str | None) -> None:
    """Launch Tau as a browser-based web UI."""
    asyncio.run(_serve(host, port, provider, model))


async def _serve(host: str, port: int, provider: str | None, model: str | None) -> None:
    from tau.console.cli import resolve_model
    from tau.modes.serve.mode import run_serve_mode
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig

    resolved_provider, resolved_model = resolve_model(model, provider)

    config = RuntimeConfig(
        cwd=Path.cwd(),
        model_id=resolved_model,
        provider=resolved_provider,
        mode="serve",
    )
    runtime = await Runtime.create(config)
    try:
        await run_serve_mode(runtime, host, port)
    finally:
        await runtime.ashutdown()
