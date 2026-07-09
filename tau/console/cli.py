from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from tau.console.commands.auth import auth
from tau.console.commands.packages import install, list_packages, remove
from tau.console.commands.update import update
from tau.settings.paths import get_app_version

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_MODES = ("interactive", "print", "json", "rpc")
_OUTPUT_FORMATS = ("text", "json")


def resolve_mode(mode: str | None, print_flag: bool, prompt: str | None, output_format: str) -> str:
    """Determine the run mode: interactive, print, json, or rpc."""
    if mode is not None:
        return mode
    if prompt is not None:
        return "json" if output_format == "json" else "print"
    if print_flag or not sys.stdout.isatty():
        return "print"
    return "interactive"


def resolve_model(model: str | None, provider: str | None) -> tuple[str | None, str | None]:
    """Parse provider/model shorthand. Explicit --provider always wins."""
    if model and provider is None and "/" in model:
        inferred_provider, _, model_id = model.partition("/")
        return inferred_provider, model_id
    return provider, model  # None when not specified; runtime falls back to settings then default


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--version", "-v", is_flag=True, default=False, help="Print version and exit.")
@click.option("--debug", "-d", is_flag=True, default=False, help="Enable debug logging.")
@click.option("--cwd", "-c", default=None, metavar="PATH", help="Set the working directory.")
@click.option(
    "--prompt",
    "-p",
    default=None,
    metavar="TEXT",
    help="Run a single prompt in non-interactive mode.",
)
@click.option(
    "--output-format",
    "-f",
    type=click.Choice(_OUTPUT_FORMATS),
    default="text",
    show_default=True,
    help="Output format for non-interactive mode (text, json).",
)
@click.option(
    "--quiet", "-q", is_flag=True, default=False, help="Hide spinner in non-interactive mode."
)
@click.option("--provider", default=None, help="Provider to use (e.g. groq, mistral, openrouter).")
@click.option(
    "--model",
    default=None,
    help="Model ID, or provider/model shorthand (e.g. groq/llama-3.3-70b-versatile).",
)
@click.option(
    "--theme",
    "-t",
    default=None,
    metavar="NAME",
    help=(
        "UI theme name (default: dark). Builtins: dark, light. See /theme for all installed themes."
    ),
)
@click.option(
    "--resume",
    "-r",
    default=None,
    metavar="[ID]",
    help=(
        "Resume a session. Omit an ID to resume the most recent; pass an ID for a specific session."
    ),
)
@click.option("--fork", "fork_session", default=None, metavar="ID", help="Fork a session by ID.")
@click.option("--session-dir", default=None, metavar="PATH", help="Session storage directory.")
@click.option("--name", "session_name", default=None, metavar="NAME", help="Session display name.")
@click.option(
    "--file",
    "files",
    multiple=True,
    hidden=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--system",
    "-s",
    default=None,
    metavar="TEXT",
    help="Replace the generated system prompt completely.",
)
@click.option(
    "--tools",
    default=None,
    metavar="NAMES",
    help="Comma-separated allowlist of tool names to enable (default: all).",
)
@click.option(
    "--ephemeral", "-e", is_flag=True, default=False, help="Don't save this session to disk."
)
@click.option(
    "--print", "print_flag", is_flag=True, default=False, help="Shorthand for --mode print."
)
@click.option(
    "--mode",
    type=click.Choice(_MODES),
    default=None,
    help="Run mode: interactive (default), print, json, rpc.",
)
@click.option(
    "--no-context-files",
    "-nc",
    is_flag=True,
    default=False,
    help="Disable AGENTS.md and CLAUDE.md discovery and loading.",
)
@click.option(
    "--approve",
    "-a",
    is_flag=True,
    default=False,
    help="Trust project-local files (extensions, settings, context files).",
)
@click.option(
    "--no-approve",
    "-na",
    is_flag=True,
    default=False,
    help="Don't trust project-local files (opposite of --approve).",
)
@click.pass_context
def cli(
    ctx: click.Context,
    version: bool,
    debug: bool,
    cwd: str | None,
    prompt: str | None,
    output_format: str,
    quiet: bool,
    provider: str | None,
    model: str | None,
    theme: str | None,
    resume: str | None,
    fork_session: str | None,
    session_dir: str | None,
    session_name: str | None,
    files: tuple[Path, ...],
    system: str | None,
    tools: str | None,
    ephemeral: bool,
    print_flag: bool,
    mode: str | None,
    no_context_files: bool,
    approve: bool,
    no_approve: bool,
) -> None:
    """Tau — an AI coding agent in your terminal."""
    if version:
        click.echo(get_app_version())
        return

    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    if cwd:
        os.chdir(cwd)

    ctx.ensure_object(dict)
    ctx.obj["prompt"] = prompt
    ctx.obj["provider"] = provider
    ctx.obj["model"] = model
    ctx.obj["theme"] = theme
    ctx.obj["resume"] = resume
    ctx.obj["fork_session"] = fork_session
    ctx.obj["session_dir"] = session_dir
    ctx.obj["session_name"] = session_name
    ctx.obj["files"] = files
    ctx.obj["system"] = system or ""
    ctx.obj["tools"] = tools
    ctx.obj["ephemeral"] = ephemeral
    ctx.obj["quiet"] = quiet
    ctx.obj["mode"] = resolve_mode(mode, print_flag, prompt, output_format)
    ctx.obj["no_context_files"] = no_context_files
    ctx.obj["approve"] = approve
    ctx.obj["no_approve"] = no_approve

    if ctx.invoked_subcommand is None:
        asyncio.run(_start(ctx.obj))


_RESUME_LATEST = "__LATEST__"


def _resolve_session_file(resume_id: str, session_dir: Path | None = None) -> Path:
    """Find a session file by its ID, searching all project session directories."""
    from tau.settings.paths import get_sessions_dir

    root = session_dir or get_sessions_dir()
    matches = list(root.rglob(f"*{resume_id}*.jsonl"))
    if not matches:
        raise click.ClickException(f"No session found with ID: {resume_id}")
    if len(matches) > 1:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0].resolve()


async def _start(opts: dict) -> None:
    """Start the runtime with the given options and run in the specified mode."""
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig
    from tau.session.manager import SessionManager

    resolved_provider, resolved_model = resolve_model(opts["model"], opts["provider"])

    resume_value: str | None = opts.get("resume")
    fork_value: str | None = opts.get("fork_session")
    custom_session_dir = (
        Path(opts["session_dir"]).expanduser().resolve() if opts.get("session_dir") else None
    )
    if resume_value and fork_value:
        raise click.ClickException("--resume and --fork cannot be used together.")
    resume_latest = resume_value == _RESUME_LATEST
    session_file: Path | None = None
    if resume_value and not resume_latest:
        session_file = _resolve_session_file(resume_value, custom_session_dir)
    if fork_value:
        source = _resolve_session_file(fork_value, custom_session_dir)
        forked = SessionManager.fork_from(source, Path.cwd(), session_dir=custom_session_dir)
        session_file = forked.session_file

    # Determine project trust from flags
    project_trusted = None
    if opts.get("approve"):
        project_trusted = True
    elif opts.get("no_approve"):
        project_trusted = False

    tools_opt = opts.get("tools")
    tool_allowlist = (
        {name.strip() for name in tools_opt.split(",") if name.strip()} if tools_opt else None
    )

    config = RuntimeConfig(
        cwd=Path.cwd(),
        model_id=resolved_model,
        provider=resolved_provider,
        resume=resume_latest,
        session_file=session_file,
        session_dir=custom_session_dir,
        persist_session=not opts["ephemeral"],
        mode=opts["mode"],
        system_prompt=opts.get("system", ""),
        tool_allowlist=tool_allowlist,
        disable_context_files=opts.get("no_context_files", False),
        project_trusted=project_trusted,
    )

    runtime = await Runtime.create(config)
    if opts.get("session_name"):
        runtime.session_manager.append_session_info(opts["session_name"])

    try:
        match opts["mode"]:
            case "interactive":
                await _run_interactive(runtime, opts["theme"])
            case "print":
                message = _build_initial_message(opts.get("prompt"), opts.get("files", ()))
                await _run_print(runtime, message, quiet=opts.get("quiet", False))
            case "json":
                message = _build_initial_message(opts.get("prompt"), opts.get("files", ()))
                await _run_json(runtime, message, quiet=opts.get("quiet", False))
            case "rpc":
                from tau.modes.rpc.mode import run_rpc_mode

                await run_rpc_mode(runtime)
    finally:
        # Emit `runtime_stop` once, in every mode, on the way out — symmetric to
        # the `runtime_ready` fired in Runtime.create.
        await runtime.ashutdown()


async def _run_interactive(runtime: Runtime, theme: str | None) -> None:
    """Run the interactive TUI mode."""
    from tau.modes.interactive.app import App

    app = await App.create(runtime, theme=theme)
    await app.run()


def _build_initial_message(message: str | None, files: tuple[Path, ...]) -> str | None:
    """Combine piped stdin, file arguments, and the explicit prompt."""
    parts: list[str] = []
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        if piped:
            parts.append(piped)
    for path in files:
        content = path.read_text(encoding="utf-8", errors="replace")
        parts.append(f'<file path="{path}">\n{content}\n</file>')
    if message:
        parts.append(message)
    return "\n\n".join(parts) or None


async def _run_print(runtime: Runtime, message: str | None, quiet: bool = False) -> None:
    """Run in print mode: send a message and print the response."""
    if not message:
        raise click.ClickException(
            'A message is required in print mode. Usage: tau --print "your prompt"'
        )

    from tau.message.types import AssistantMessage

    result: AssistantMessage | None = None
    settled = asyncio.Event()

    async def on_message_end(event: object) -> None:
        """Capture the final assistant message."""
        nonlocal result
        msg = getattr(event, "message", None)
        if isinstance(msg, AssistantMessage):
            result = msg

    async def on_settled(_event: object) -> None:
        """Signal that processing is complete."""
        settled.set()

    hooks = runtime.hooks
    unsub_msg = hooks.register("message_end", on_message_end)
    unsub_settled = hooks.register("settled", on_settled)

    try:
        await runtime.invoke(message)
        await settled.wait()
    finally:
        unsub_msg()
        unsub_settled()

    if result is None:
        raise click.ClickException("No response received.")

    if result.error:
        raise click.ClickException(result.error)

    click.echo(result.text_content(), nl=False)


async def _run_json(runtime: Runtime, message: str | None, quiet: bool = False) -> None:
    """Run in JSON mode: send a message and return structured JSON output."""
    if not message:
        raise click.ClickException(
            'A message is required in json mode. Usage: tau --mode json "your prompt"'
        )

    import dataclasses
    import json

    from tau.hooks.types import SettledEvent

    settled = asyncio.Event()

    def _serialize(event: object) -> str:
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            return json.dumps(dataclasses.asdict(event))
        return json.dumps({"type": type(event).__name__})

    async def on_event(event: object) -> None:
        """Output event as JSON and signal when settled."""
        click.echo(_serialize(event))
        if isinstance(event, SettledEvent):
            settled.set()

    hooks = runtime.hooks
    hook_names = [
        "agent_start",
        "agent_end",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "settled",
    ]
    unsubs = [hooks.register(name, on_event) for name in hook_names]

    try:
        await runtime.invoke(message)
        await settled.wait()
    finally:
        for unsub in unsubs:
            unsub()


cli.add_command(auth)
cli.add_command(install)
cli.add_command(remove)
cli.add_command(update)
cli.add_command(list_packages, name="list")


def _rewrite_args(argv: list[str]) -> list[str]:
    """Normalize optional resume values and ``@file`` arguments.

    click only supports required or absent values for options, so we pre-process
    sys.argv before click sees it:
      --resume         → --resume __LATEST__   (resume most recent)
      --resume <id>    → --resume <id>          (resume specific session)
      @README.md       → --file README.md
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("@") and len(arg) > 1:
            out.extend(["--file", arg[1:]])
            i += 1
        elif arg in ("--resume", "-r"):
            out.append("--resume")
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                out.append(argv[i + 1])
                i += 2
            else:
                out.append(_RESUME_LATEST)
                i += 1
        else:
            out.append(arg)
            i += 1
    return out


def main() -> None:
    """Entry point for the CLI."""
    import sys

    sys.argv[1:] = _rewrite_args(sys.argv[1:])
    cli()
