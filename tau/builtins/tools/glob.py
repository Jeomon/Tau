from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.builtins.tools.utils import run_bounded_lines
from tau.tool.render import call_line
from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


def _render_glob_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("glob", args.get("pattern", ""))


_MAX_RESULTS = 1000
_PREVIEW_LINES = 5


def _render_glob_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import DIM, RESET

    metadata = opts.metadata or {}
    match_count = metadata.get("match_count", 0)
    truncated = metadata.get("truncated", False)

    if match_count == 0:
        return ["No files matched"]

    file_word = "file" if match_count == 1 else "files"
    summary = f"Found {match_count} {file_word}"
    if truncated:
        summary += f"  {DIM}(truncated){RESET}"

    lines = [line for line in content.splitlines() if line and not line.startswith("[")]
    result = [summary]

    show = lines if opts.expanded else lines[:_PREVIEW_LINES]
    for path in show:
        result.append(path)

    if opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  (ctrl+o to collapse){RESET}")
    elif not opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  ···  (ctrl+o to expand){RESET}")

    return result


class GlobParams(BaseModel):
    """Parameters for the glob tool."""

    pattern: str = Field(
        description="Glob pattern (e.g. 'src/**/*.py').",
        examples=["src/**/*.py", "**/*.ts", "tests/**/test_*.py"],
    )
    path: str = Field(
        default="",
        description="Base directory to search from. Defaults to the agent's cwd.",
        examples=["/home/user/project", "/home/user/project/src"],
    )


class GlobTool(Tool):
    """Tool for finding files matching glob patterns."""

    def __init__(self) -> None:
        super().__init__(
            name="glob",
            description=(
                "Find files matching a glob pattern. Returns absolute paths, one per line, "
                f"up to {_MAX_RESULTS} results. Supports ** for recursive matching."
            ),
            schema=GlobParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_glob_result,
            render_call=_render_glob_call,
            render_shell="default",
            prompt_guidelines="Use to discover files by pattern before reading or editing them.",
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Get a short display name for the glob operation."""
        return args.get("pattern", "glob")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = GlobParams.model_validate(invocation.params)
        base = Path(params.path or invocation.cwd or ".").resolve()

        if not base.is_dir():
            return ToolResult.error(invocation.id, f"Base path is not a directory: {base}")

        try:
            matches = await self._rg_files(params.pattern, base, signal)
        except FileNotFoundError:
            return ToolResult.error(
                invocation.id,
                "ripgrep (rg) is required but was not found.",
            )
        except RuntimeError as error:
            return ToolResult.error(invocation.id, str(error))

        truncated = len(matches) > _MAX_RESULTS
        matches = sorted(matches[:_MAX_RESULTS])

        metadata = {
            "pattern": params.pattern,
            "match_count": len(matches),
            "truncated": truncated,
        }

        if not matches:
            return ToolResult.ok(
                invocation.id, f"No files matched pattern: {params.pattern}", metadata=metadata
            )

        result = "\n".join(matches)
        if truncated:
            result += f"\n\n[Results truncated at {_MAX_RESULTS}. Narrow your pattern.]"

        return ToolResult.ok(invocation.id, result, metadata=metadata)

    async def _rg_files(self, pattern: str, base: Path, signal: AbortSignal | None) -> list[str]:
        cmd = ["rg", "--files", "--glob", pattern, str(base)]
        returncode, lines, cancelled = await run_bounded_lines(
            cmd, max_lines=_MAX_RESULTS, signal=signal
        )
        if cancelled:
            raise RuntimeError("File search cancelled.")
        if returncode not in (0, 1) and len(lines) <= _MAX_RESULTS:
            error = "\n".join(lines).strip() or f"ripgrep exited with status {returncode}."
            raise RuntimeError(error)
        return [line for line in lines if line]
