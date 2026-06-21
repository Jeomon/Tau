from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

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

_DEFAULT_TIMEOUT = 120


def _render_terminal_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("terminal", args.get("command", ""))


_PREVIEW_LINES = 5


def _render_terminal_result(content: str, opts: Any) -> list[str]:
    from tau.tui.ansi import DIM, RED, RESET

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

    show = lines if opts.expanded else lines[:_PREVIEW_LINES]
    result = [fmt(show[0])]
    for line in show[1:]:
        result.append(fmt(line))

    if opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  (ctrl+o to collapse){RESET}")
    elif not opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  ···  (ctrl+o to expand){RESET}")

    return result


class TerminalParams(BaseModel):
    """Parameters for terminal command execution."""

    command: str = Field(description="Shell command to execute.")
    timeout: int = Field(
        default=_DEFAULT_TIMEOUT,
        ge=1,
        le=600,
        description=f"Timeout in seconds (default {_DEFAULT_TIMEOUT}, max 600).",
    )


class TerminalTool(Tool):
    """Tool for executing shell commands."""

    def __init__(self) -> None:
        super().__init__(
            name="terminal",
            description=(
                "Execute a shell command and return its combined stdout+stderr output. "
                "Commands run in the agent's working directory. "
                "Avoid interactive commands or those that require user input."
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
        cmd = args.get("command", "")
        return cmd[:60] + ("…" if len(cmd) > 60 else "")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute a shell command and return the output."""
        params = TerminalParams.model_validate(invocation.params)
        cwd = invocation.cwd or None

        sm = context.settings if context is not None else None
        shell_path = sm.get_shell_path() if sm is not None else None
        shell_prefix = sm.get_shell_command_prefix() if sm is not None else None

        command = params.command
        if shell_prefix:
            command = f"{shell_prefix}\n{command}"

        try:
            if shell_path:
                proc = await asyncio.create_subprocess_exec(
                    shell_path,
                    "-c",
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=params.timeout)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult.error(
                    invocation.id,
                    f"Command timed out after {params.timeout}s: {params.command}",
                    metadata={
                        "command": params.command,
                        "exit_code": -1,
                        "timed_out": True,
                        "output_length": 0,
                    },
                )
        except OSError as e:
            return ToolResult.error(invocation.id, f"Failed to start command: {e}")

        output = stdout.decode("utf-8", errors="replace")
        rc = proc.returncode or 0
        metadata = {
            "command": params.command,
            "exit_code": rc,
            "timed_out": False,
            "output_length": len(output),
        }

        if rc != 0:
            return ToolResult(
                id=invocation.id,
                content=output or f"(exit code {rc}, no output)",
                is_error=True,
                metadata=metadata,
            )
        return ToolResult.ok(invocation.id, output or "(no output)", metadata=metadata)
