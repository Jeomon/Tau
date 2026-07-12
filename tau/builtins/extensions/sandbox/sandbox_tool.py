"""Terminal tool variant that executes inside a microsandbox microVM.

Same name, schema, and description as the built-in ``terminal`` tool — the
agent's interface and prompt guidance are unchanged. Only ``execute()``
differs: the command runs inside an isolated microVM (own kernel, bind-mounted
project directory) instead of a host subprocess. Falls back to the real
built-in tool whenever the sandbox can't be used.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from manager import (  # type: ignore[import-not-found]
    WORKDIR,
    SandboxManager,
    SandboxUnavailableError,
)

from tau.builtins.tools.terminal import TerminalParams, TerminalTool
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

_MAX_OUTPUT_BYTES = 50 * 1024
_MAX_OUTPUT_LINES = 2_000


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    return call_line("terminal", args.get("cmd", ""), "sandboxed")


def _render_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import RED, RESET

    metadata = opts.metadata or {}
    exit_code = metadata.get("exit_code", 0)
    timed_out = metadata.get("timed_out", False)
    failed = opts.is_error or timed_out or exit_code != 0

    if timed_out:
        return [f"{RED}Timed out{RESET}"]

    lines = content.splitlines() if content else []
    if not lines:
        return [f"{RED}Failed (exit {exit_code}, no output){RESET}" if failed else "(no output)"]

    def fmt(line: str) -> str:
        return f"{RED}{line}{RESET}" if failed else line

    return [fmt(line) for line in lines]


@dataclass
class _Attempt:
    cancelled: bool = False
    timed_out: bool = False
    stale: bool = False  # sandbox was idle-timed-out or externally removed
    output: Any = None
    error_message: str | None = None


def _truncate(text: str, max_bytes: int, max_lines: int) -> tuple[str, bool]:
    lines = text.splitlines()
    truncated = False
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        truncated = True
    text = "\n".join(lines)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        text = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        truncated = True
    return text, truncated


class SandboxTerminalTool(Tool):
    """Drop-in replacement for the built-in ``terminal`` tool, sandboxed."""

    def __init__(self, manager: SandboxManager, runtime_ref: Any) -> None:
        self._manager = manager
        self._runtime_ref = runtime_ref
        self._fallback = TerminalTool()
        self._warned_unavailable = False
        super().__init__(
            name="terminal",
            description=self._fallback.description,
            schema=TerminalParams,
            kind=ToolKind.Execute,
            render_result=_render_result,
            render_call=_render_call,
            render_shell="default",
            prompt_guidelines=self._fallback.prompt_guidelines,
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        return self._fallback.get_display_name(args)

    def _notify(self, message: str, type: str = "warning") -> None:  # noqa: A002
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        from tau.extensions.context import ExtensionContext

        ui = ExtensionContext.from_runtime(runtime).ui
        if ui is not None:
            ui.notify(message, type=type)

    async def _run_once(
        self, sandbox: Any, cmd: str, timeout: int, signal: AbortSignal | None
    ) -> _Attempt:
        import microsandbox

        attempt = _Attempt()
        exec_task = asyncio.ensure_future(
            sandbox.exec("sh", ["-c", cmd], cwd=WORKDIR, timeout=float(timeout))
        )
        signal_task = asyncio.ensure_future(signal.wait()) if signal is not None else None
        waiters = {exec_task} | ({signal_task} if signal_task is not None else set())

        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if signal_task is not None and signal_task in done:
                attempt.cancelled = True
                if not self._manager.config.persistent:
                    await self._manager.reset()
                else:
                    exec_task.cancel()
            else:
                attempt.output = exec_task.result()
        except microsandbox.ExecTimeoutError:
            attempt.timed_out = True
        except (microsandbox.SandboxNotRunningError, microsandbox.SandboxNotFoundError):
            # The runtime's own idle_timeout stopped the VM (or it was removed
            # externally) between manager.get() and this call — not a real
            # failure, the caller reboots and retries once.
            attempt.stale = True
        except Exception as e:
            attempt.error_message = str(e)
        finally:
            for task in waiters:
                if not task.done():
                    task.cancel()

        return attempt

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        try:
            sandbox = await self._manager.get()
        except SandboxUnavailableError as e:
            if not self._warned_unavailable:
                self._warned_unavailable = True
                self._notify(f"Sandbox unavailable ({e}); running commands on the host instead.")
            return await self._fallback.execute(
                invocation, tool_execution_update_callback, signal, context
            )

        params = TerminalParams.model_validate(invocation.params)

        attempt = await self._run_once(sandbox, params.cmd, params.timeout, signal)
        if attempt.stale:
            await self._manager.reset()
            try:
                sandbox = await self._manager.get()
            except SandboxUnavailableError as e:
                self._notify(f"Sandbox unavailable ({e}); running commands on the host instead.")
                return await self._fallback.execute(
                    invocation, tool_execution_update_callback, signal, context
                )
            attempt = await self._run_once(sandbox, params.cmd, params.timeout, signal)

        if attempt.cancelled:
            note = "[Command cancelled]"
            content = note if self._manager.config.persistent else f"{note}\n(sandbox restarted)"
            return ToolResult(
                id=invocation.id, content=content, is_error=True, metadata={"cancelled": True}
            )

        if attempt.timed_out:
            note = f"[Command timed out after {params.timeout} seconds]"
            return ToolResult(
                id=invocation.id,
                content=note,
                is_error=True,
                metadata={"timed_out": True, "command": params.cmd},
            )

        if attempt.stale or attempt.error_message is not None:
            reason = attempt.error_message or "sandbox unavailable after retry"
            return ToolResult.error(invocation.id, f"Sandbox execution failed: {reason}")

        output = attempt.output
        assert output is not None
        combined = (output.stdout_text or "") + (output.stderr_text or "")
        text, truncated = _truncate(combined, _MAX_OUTPUT_BYTES, _MAX_OUTPUT_LINES)
        display = text or "(no output)"
        if truncated:
            display = f"{display.rstrip()}\n\n[Output truncated]"

        metadata = {
            "command": params.cmd,
            "exit_code": output.exit_code,
            "timed_out": False,
            "cancelled": False,
            "sandboxed": True,
        }

        if output.exit_code != 0:
            note = f"[Command exited with code {output.exit_code}]"
            return ToolResult(
                id=invocation.id,
                content=f"{display}\n\n{note}",
                is_error=True,
                metadata=metadata,
            )

        return ToolResult.ok(invocation.id, display, metadata=metadata)
