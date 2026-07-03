from __future__ import annotations

import asyncio
import contextlib
import os
import signal as signal_module
import subprocess
import time
from typing import Any

from pydantic import BaseModel, Field

from tau.builtins.tools.utils import OutputAccumulator, OutputSnapshot
from tau.tool.render import call_line
from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

_DEFAULT_TIMEOUT = 30


def _render_terminal_call(args: dict, _streaming: bool = False) -> list[str]:
    return call_line("terminal", args.get("cmd", ""))


_MAX_OUTPUT_BYTES = 50 * 1024
_MAX_OUTPUT_LINES = 2_000
_UPDATE_INTERVAL_SECONDS = 0.1


def _render_terminal_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import RED, RESET

    metadata = opts.metadata or {}
    exit_code = metadata.get("exit_code", 0)
    timed_out = metadata.get("timed_out", False)
    failed = timed_out or exit_code != 0

    if timed_out:
        return [f"{RED}Timed out{RESET}"]

    lines = content.splitlines() if content else []
    if not lines:
        return [f"{RED}Failed (exit {exit_code}, no output){RESET}" if failed else "(no output)"]

    def fmt(line: str) -> str:
        return f"{RED}{line}{RESET}" if failed else line

    result = [fmt(lines[0])]
    for line in lines[1:]:
        result.append(fmt(line))

    return result


class TerminalParams(BaseModel):
    """Parameters for terminal command execution."""

    cmd: str = Field(
        description=("Non-interactive shell command to execute in the agent's working directory."),
        examples=["python -m pytest tests/", "ruff check src/", "git status"],
    )
    timeout: int = Field(
        default=_DEFAULT_TIMEOUT,
        ge=1,
        description=f"Timeout in seconds (default {_DEFAULT_TIMEOUT}).",
        examples=[30, 120, 300],
    )


class TerminalTool(Tool):
    """Tool for executing shell commands."""

    def __init__(self) -> None:
        super().__init__(
            name="terminal",
            description=(
                "Execute a non-interactive shell command in the agent's working directory. "
                "Returns the combined stdout and stderr tail, retaining at most 50 KiB or "
                "2,000 lines. Commands that require user input are unsupported."
            ),
            schema=TerminalParams,
            kind=ToolKind.Execute,
            render_result=_render_terminal_result,
            render_call=_render_terminal_call,
            render_shell="default",
            prompt_guidelines=(
                "Run tests or the build after making code changes to verify correctness."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Get a short display name for the command."""
        cmd = args.get("cmd", "")
        return cmd[:60] + ("…" if len(cmd) > 60 else "")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute a shell command and stream output incrementally to the TUI."""
        params = TerminalParams.model_validate(invocation.params)
        cwd = invocation.cwd or None

        sm = context.settings if context is not None else None
        shell_path = sm.get_shell_path() if sm is not None else None
        shell_prefix = sm.get_shell_command_prefix() if sm is not None else None

        command = params.cmd
        if shell_prefix:
            command = f"{shell_prefix}\n{command}"

        spawn_options: dict[str, Any] = {}
        if os.name == "nt":
            spawn_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            spawn_options["start_new_session"] = True

        try:
            if shell_path:
                proc = await asyncio.create_subprocess_exec(
                    shell_path,
                    "-c",
                    command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    **spawn_options,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    **spawn_options,
                )
        except OSError as e:
            return ToolResult.error(invocation.id, f"Failed to start command: {e}")

        output = OutputAccumulator(
            max_bytes=_MAX_OUTPUT_BYTES,
            max_lines=_MAX_OUTPUT_LINES,
            temp_file_prefix="tau-terminal-",
        )
        timed_out = False
        cancelled = False
        update_task: asyncio.Task[None] | None = None
        update_dirty = False
        last_update_at = 0.0
        update_lock = asyncio.Lock()

        def _metadata(snapshot: OutputSnapshot, *, running: bool) -> dict[str, Any]:
            return {
                "command": params.cmd,
                "running": running,
                "output_length": snapshot.total_bytes,
                "truncated": snapshot.truncated,
                "full_output_path": snapshot.full_output_path,
            }

        def _display_content(snapshot: OutputSnapshot) -> str:
            content = snapshot.content
            if snapshot.truncated and snapshot.full_output_path:
                footer = f"[Output truncated. Full output: {snapshot.full_output_path}]"
                return f"{content.rstrip()}\n\n{footer}" if content else footer
            return content

        async def _emit_update(*, force: bool = False) -> None:
            nonlocal update_dirty, last_update_at
            if tool_execution_update_callback is None or (not update_dirty and not force):
                return
            async with update_lock:
                if not update_dirty and not force:
                    return
                update_dirty = False
                last_update_at = time.monotonic()
                snapshot = output.snapshot()
                await tool_execution_update_callback(
                    ToolResult.ok(
                        invocation.id,
                        _display_content(snapshot),
                        metadata=_metadata(snapshot, running=True),
                    )
                )

        async def _delayed_update(delay: float) -> None:
            try:
                await asyncio.sleep(delay)
                await _emit_update()
            except asyncio.CancelledError:
                pass

        async def _schedule_update() -> None:
            nonlocal update_dirty, update_task
            if tool_execution_update_callback is None:
                return
            update_dirty = True
            delay = _UPDATE_INTERVAL_SECONDS - (time.monotonic() - last_update_at)
            if delay <= 0:
                if update_task is not None:
                    update_task.cancel()
                    update_task = None
                await _emit_update()
            elif update_task is None or update_task.done():
                update_task = asyncio.create_task(_delayed_update(delay))

        async def _read_loop() -> None:
            nonlocal cancelled
            assert proc.stdout is not None
            while True:
                if signal is not None and signal.is_set():
                    cancelled = True
                    break
                read_task = asyncio.create_task(proc.stdout.read(8192))
                signal_task = asyncio.create_task(signal.wait()) if signal is not None else None
                waiters: set[asyncio.Task[Any]] = {read_task}
                if signal_task is not None:
                    waiters.add(signal_task)
                try:
                    done, _ = await asyncio.wait(
                        waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if signal_task is not None and signal_task in done:
                        cancelled = True
                        break
                    data = read_task.result()
                finally:
                    for task in waiters:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
                if not data:
                    break
                output.append(data)
                await _schedule_update()

        try:
            try:
                if tool_execution_update_callback is not None:
                    await _emit_update(force=True)
                await asyncio.wait_for(_read_loop(), timeout=params.timeout)
            except TimeoutError:
                timed_out = True
            finally:
                if proc.returncode is None and (timed_out or cancelled):
                    await _terminate_process_tree(proc)
                if proc.stdout is not None and (timed_out or cancelled):
                    with contextlib.suppress(Exception):
                        remaining = await asyncio.wait_for(proc.stdout.read(), timeout=1)
                        if remaining:
                            output.append(remaining)
                await proc.wait()

            if update_task is not None:
                update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await update_task
            snapshot = output.finish()
        finally:
            # Guarantee resource release even if _read_loop raised something
            # other than TimeoutError (e.g. CancelledError): finish() above is
            # skipped on that path, so close the spill fd/temp file and cancel
            # any dangling update task here.
            if update_task is not None and not update_task.done():
                update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await update_task
            output.close()
        output_text = _display_content(snapshot)
        metadata = {
            **_metadata(snapshot, running=False),
            "exit_code": proc.returncode if proc.returncode is not None else -1,
            "timed_out": timed_out,
            "cancelled": cancelled,
        }

        if timed_out:
            result = ToolResult(
                id=invocation.id,
                content=output_text or "(no output before timeout)",
                is_error=True,
                metadata=metadata,
            )
        elif cancelled:
            result = ToolResult(
                id=invocation.id,
                content=output_text or "(no output before cancellation)",
                is_error=True,
                metadata=metadata,
            )
        elif proc.returncode not in (0, None):
            result = ToolResult(
                id=invocation.id,
                content=output_text or f"(exit code {proc.returncode}, no output)",
                is_error=True,
                metadata=metadata,
            )
        else:
            result = ToolResult.ok(
                invocation.id,
                output_text or "(no output)",
                metadata=metadata,
            )

        if tool_execution_update_callback is not None:
            await tool_execution_update_callback(result)
        return result


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Forcefully terminate a subprocess and its descendants."""
    if os.name == "nt":
        if proc.pid is not None:
            with contextlib.suppress(OSError):
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(proc.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
    elif proc.pid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal_module.SIGKILL)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
