from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import click


@click.command("web")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind the web server to.",
)
@click.option(
    "--port",
    "-p",
    default=8079,
    show_default=True,
    type=int,
    help="Port to bind the web server to.",
)
@click.option("--provider", default=None, help="Provider to use (e.g. groq, mistral, openrouter).")
@click.option(
    "--model",
    default=None,
    help="Model ID, or provider/model shorthand (e.g. groq/llama-3.3-70b-versatile).",
)
@click.option(
    "--reload",
    "reload",
    is_flag=True,
    default=False,
    help="Restart the web process when tau/modes/web Python files change.",
)
def web(host: str, port: int, provider: str | None, model: str | None, reload: bool) -> None:
    """Launch Tau as a browser-based web UI."""
    if reload:
        _web_reload(host, port, provider, model)
        return
    asyncio.run(_web(host, port, provider, model))


def _web_reload(host: str, port: int, provider: str | None, model: str | None) -> None:
    """Run a development reload supervisor for the browser UI."""
    from watchfiles import watch

    watch_path = Path(__file__).parents[2] / "modes" / "web"
    command = [sys.argv[0], "web", "--host", host, "--port", str(port)]
    if provider:
        command.extend(["--provider", provider])
    if model:
        command.extend(["--model", model])

    def start() -> subprocess.Popen[bytes]:
        click.echo(f"Tau web reload watching {watch_path}")
        return subprocess.Popen(command)

    def stop(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    process = start()
    try:
        for changes in watch(
            watch_path,
            rust_timeout=500,
            yield_on_timeout=True,
            recursive=True,
        ):
            if process.poll() is not None:
                raise click.ClickException(f"web process exited with code {process.returncode}")
            if not changes:
                continue
            if not any(path.endswith(".py") for _, path in changes):
                continue
            click.echo("Restarting Tau web UI after source change")
            stop(process)
            process = start()
    except KeyboardInterrupt:
        pass
    finally:
        stop(process)


async def _web(host: str, port: int, provider: str | None, model: str | None) -> None:
    from tau.console.cli import resolve_model
    from tau.modes.web.app import App
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig

    resolved_provider, resolved_model = resolve_model(model, provider)

    config = RuntimeConfig(
        cwd=Path.cwd(),
        model_id=resolved_model,
        provider=resolved_provider,
        mode="web",
    )
    runtime = await Runtime.create(config)
    try:
        app = await App.create(runtime, host=host, port=port)
        await app.run()
    finally:
        await runtime.ashutdown()
