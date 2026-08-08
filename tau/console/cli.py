from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from tau.console.commands.auth import auth
from tau.console.commands.doctor import doctor
from tau.console.commands.packages import install, list_packages, remove
from tau.console.commands.update import update
from tau.modes.signals import Interrupted
from tau.settings.paths import get_app_version

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_MODES = ("interactive", "print", "json", "rpc", "remote")
_OUTPUT_FORMATS = ("text", "json")

# On Windows, stdio is often bound to a legacy codepage (e.g. cp1252) that can't
# encode arbitrary Unicode (e.g. a zero-width space embedded in a COM error
# message). Without this, such characters crash the log call itself and mask
# the original error behind a "Logging error" traceback.
#
# isinstance rather than hasattr: reconfigure() lives on TextIOWrapper, which is
# what the real console streams are, and the narrower check is one a type
# checker can follow. A replaced stdout (pytest capture, a redirect) is left
# alone, which is the safe default — this only exists for the console.
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(errors="backslashreplace")


def resolve_mode(
    mode: str | None, print_flag: bool, prompt: tuple[str, ...], output_format: str
) -> str:
    """Determine the run mode: interactive, print, json, or rpc."""
    if mode is not None:
        return mode
    if prompt:
        return "json" if output_format == "json" else "print"
    # Interactive needs a tty on *both* ends: the TUI puts stdin into raw mode
    # (termios.tcgetattr on a pipe raises "Inappropriate ioctl for device") and
    # paints stdout. Piped stdin is already prompt input as far as
    # _build_messages is concerned, so treat it as a headless run rather than
    # starting a TUI that has no keyboard and dies on entry.
    if print_flag or not sys.stdout.isatty() or not sys.stdin.isatty():
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
@click.option(
    "--startup",
    is_flag=True,
    default=False,
    help="Print per-phase startup timing diagnostics to stderr "
    "(settings, model/LLM, session manager, resources, extensions, agent).",
)
@click.option("--cwd", "-c", default=None, metavar="PATH", help="Set the working directory.")
@click.option(
    "--prompt",
    "-p",
    multiple=True,
    metavar="TEXT",
    help="Run a prompt in non-interactive mode. Repeat to send several in order.",
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
    "--json-events",
    type=click.Choice(["compact", "full"]),
    default="compact",
    show_default=True,
    help="Event set for json output: compact (essentials) or full (everything RPC sends).",
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
    "--base-url",
    default=None,
    metavar="URL",
    help="Temporarily override the base URL for this run's provider (not persisted).",
)
@click.option(
    "--effort",
    "thinking_level",
    type=click.Choice(("off", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")),
    default=None,
    help="Temporarily override the thinking/reasoning effort level for this run "
    "(not persisted; clamped to what the selected model actually supports).",
)
@click.option(
    "--theme",
    "-t",
    default=None,
    metavar="NAME",
    help=(
        "UI theme name (default: dark), or 'auto' to follow the terminal background. "
        "See /theme for all installed themes."
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
    "--append-system-prompt",
    default=None,
    metavar="TEXT",
    help="Append text to the system prompt. Applies whether the prompt is generated "
    "or replaced with --system.",
)
@click.option(
    "--tools",
    default=None,
    metavar="NAMES",
    help="Comma-separated allowlist of tool names to enable (default: all).",
)
@click.option(
    "--exclude-tools",
    default=None,
    metavar="NAMES",
    help="Comma-separated tool names to disable. Applied after --tools.",
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
    help="Run mode: interactive (default), print, json, rpc, remote.",
)
@click.option(
    "--socket",
    "socket_path",
    default=None,
    metavar="PATH",
    help="Unix socket for --mode remote (default: a path named for the session).",
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
    startup: bool,
    cwd: str | None,
    prompt: tuple[str, ...],
    output_format: str,
    json_events: str,
    quiet: bool,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    thinking_level: str | None,
    theme: str | None,
    resume: str | None,
    fork_session: str | None,
    session_dir: str | None,
    session_name: str | None,
    files: tuple[Path, ...],
    system: str | None,
    append_system_prompt: str | None,
    tools: str | None,
    exclude_tools: str | None,
    ephemeral: bool,
    print_flag: bool,
    mode: str | None,
    socket_path: str | None,
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

    if startup:
        from tau.utils import timing

        timing.enable()

    if cwd:
        os.chdir(cwd)

    ctx.ensure_object(dict)
    ctx.obj["prompt"] = prompt
    ctx.obj["json_events"] = json_events
    ctx.obj["provider"] = provider
    ctx.obj["model"] = model
    ctx.obj["base_url"] = base_url
    ctx.obj["thinking_level"] = thinking_level
    ctx.obj["theme"] = theme
    ctx.obj["resume"] = resume
    ctx.obj["fork_session"] = fork_session
    ctx.obj["session_dir"] = session_dir
    ctx.obj["session_name"] = session_name
    ctx.obj["files"] = files
    ctx.obj["system"] = system or ""
    ctx.obj["append_system_prompt"] = append_system_prompt or ""
    ctx.obj["tools"] = tools
    ctx.obj["exclude_tools"] = exclude_tools
    ctx.obj["ephemeral"] = ephemeral
    ctx.obj["quiet"] = quiet
    ctx.obj["mode"] = resolve_mode(mode, print_flag, prompt, output_format)
    ctx.obj["socket_path"] = socket_path
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

    def _name_set(value: str | None) -> set[str]:
        return {name.strip() for name in value.split(",") if name.strip()} if value else set()

    tools_opt = opts.get("tools")
    tool_allowlist = _name_set(tools_opt) if tools_opt else None
    excluded_tools = _name_set(opts.get("exclude_tools"))

    config = RuntimeConfig(
        cwd=Path.cwd(),
        model_id=resolved_model,
        provider=resolved_provider,
        base_url=opts.get("base_url"),
        thinking_level=opts.get("thinking_level"),
        resume=resume_latest,
        session_file=session_file,
        session_dir=custom_session_dir,
        persist_session=not opts["ephemeral"],
        mode=opts["mode"],
        system_prompt=opts.get("system", ""),
        append_system_prompt=opts.get("append_system_prompt", ""),
        tool_allowlist=tool_allowlist,
        exclude_tools=excluded_tools,
        disable_context_files=opts.get("no_context_files", False),
        project_trusted=project_trusted,
    )

    if opts["mode"] in ("rpc", "json"):
        # Claim stdout before anything else can write to it — extensions load
        # (and may print) during Runtime.create, which would otherwise land
        # non-JSON lines in the protocol stream before the mode even starts.
        from tau.modes.wire import install_output_guard

        install_output_guard()

    # First-run gate: sampled before Runtime.create, which may write settings
    # and would otherwise make the very first launch look like a repeat one.
    from tau.settings.paths import get_settings_path

    first_run_setup = opts["mode"] == "interactive" and not get_settings_path().exists()

    runtime = await Runtime.create(config)

    from tau.utils import timing

    timing.print_report()

    if opts.get("session_name"):
        await runtime.set_session_name(opts["session_name"])

    # Interactive mode manages its own log file (it also has to strip
    # terminal-writing handlers and restore them on exit); every other mode
    # gets a plain one here so the path advertised in the system prompt is
    # backed by an actual file regardless of which mode is running.
    if opts["mode"] != "interactive" and runtime.session_manager.session_id:
        from tau.utils.logging import attach_session_log_file

        attach_session_log_file(runtime.session_manager.session_id)

    try:
        match opts["mode"]:
            case "interactive":
                await _run_interactive(runtime, opts["theme"], first_run_setup)
            case "print" | "json":
                from tau.modes.print.mode import run_print_mode

                await run_print_mode(
                    runtime,
                    _build_messages(opts.get("prompt", ()), opts.get("files", ())),
                    output=opts["mode"],
                    json_events=opts.get("json_events", "compact"),
                )
            case "rpc":
                from tau.modes.rpc.mode import run_rpc_mode

                await run_rpc_mode(runtime)
            case "remote":
                from tau.modes.remote.mode import run_remote_mode

                await run_remote_mode(runtime, opts.get("socket_path"))
    except Interrupted as exc:
        # A signalled headless run — print, json or rpc. The turn was aborted
        # and the session written out, so report the conventional exit code
        # rather than a traceback, and let a supervisor tell a killed run from
        # one whose client simply went away. `finally` still emits
        # `runtime_stop`.
        raise SystemExit(exc.code) from None
    except click.ClickException:
        # Deliberate user-facing validation errors, not bugs — Click already
        # renders these; logging them as a crash would be noise.
        raise
    except Exception:
        # An exception that escapes here would otherwise only reach Python's
        # default excepthook (stderr, never through `logging`), invisible to
        # the session log file. Log it before re-raising so the crash is
        # still fatal but the traceback survives in the file for debugging.
        logging.getLogger(__name__).exception("Unhandled error in %s mode", opts["mode"])
        raise
    finally:
        # Emit `runtime_stop` once, in every mode, on the way out — symmetric to
        # the `runtime_ready` fired in Runtime.create.
        await runtime.ashutdown()


async def _run_interactive(
    runtime: Runtime, theme: str | None, first_run_setup: bool = False
) -> None:
    """Run the interactive TUI mode."""
    from tau.modes.interactive.app import App

    app = await App.create(runtime, theme=theme, first_run_setup=first_run_setup)
    await app.run()


def _build_messages(prompts: tuple[str, ...], files: tuple[Path, ...]) -> list[str]:
    """Build the prompt sequence for a non-interactive run.

    Piped stdin and ``--file`` contents are context for the *first* prompt, so
    they are folded into it; each additional ``--prompt`` is sent on its own
    afterwards, against the same session.
    """
    parts: list[str] = []
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        if piped:
            parts.append(piped)
    for path in files:
        content = path.read_text(encoding="utf-8", errors="replace")
        parts.append(f'<file path="{path}">\n{content}\n</file>')
    rest = list(prompts)
    if rest:
        parts.append(rest.pop(0))
    first = "\n\n".join(parts)
    return ([first] if first else []) + rest


cli.add_command(auth)
cli.add_command(doctor)
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
      --continue       → --resume __LATEST__   (alias; -c is already --cwd)
      @README.md       → --file README.md
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("@") and len(arg) > 1:
            out.extend(["--file", arg[1:]])
            i += 1
        elif arg == "--continue":
            # Spelled --continue in most agent CLIs, and the muscle memory is
            # worth honouring. Deliberately id-less: --resume already covers
            # "this specific session", so anything following it is left alone.
            out.extend(["--resume", _RESUME_LATEST])
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
