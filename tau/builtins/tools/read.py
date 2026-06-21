from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

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


def _render_read_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("read", args.get("path", ""))


class ReadParams(BaseModel):
    """Parameters for the read tool."""

    path: str = Field(description="Absolute path to the file to read.")
    offset: int = Field(default=0, ge=0, description="Line number to start reading from (0-based).")
    limit: int = Field(default=2000, ge=1, description="Maximum number of lines to read.")


_PREVIEW_LINES = 5


def _render_read_result(content: str, opts: Any) -> list[str]:
    from tau.tui.ansi import DIM, RESET

    metadata = opts.metadata or {}
    lines_returned = metadata.get("lines_returned", 0)
    truncated = metadata.get("truncated", False)

    line_word = "line" if lines_returned == 1 else "lines"
    result = [f"Read {lines_returned} {line_word}"]

    parsed = []
    for raw in content.splitlines():
        if "\t" in raw:
            num, _, text = raw.partition("\t")
            parsed.append((num.strip(), text))

    if not parsed:
        return result

    show = parsed if opts.expanded else parsed[:_PREVIEW_LINES]
    for num, text in show:
        result.append(f"{DIM}{num}{RESET}  {text}")

    if opts.expanded and (len(parsed) > _PREVIEW_LINES or truncated):
        result.append(f"{DIM}  (ctrl+o to collapse){RESET}")
    elif not opts.expanded and (len(parsed) > _PREVIEW_LINES or truncated):
        result.append(f"{DIM}  ···  (ctrl+o to expand){RESET}")

    return result


class ReadTool(Tool):
    """Tool for reading file contents with line numbers."""

    def __init__(self) -> None:
        super().__init__(
            name="read",
            description=(
                "Read the contents of a file. Returns lines with 1-based line numbers"
                " in the format '<n>\\t<content>'."
                " Use offset and limit to read large files in chunks."
            ),
            schema=ReadParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_read_result,
            render_call=_render_read_call,
            render_shell="default",
            prompt_guidelines=(
                "Use grep first to locate the relevant section,"
                " then read with offset/limit instead of loading the entire file."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Get a short display name for the read operation."""
        return args.get("path", "read")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the file read operation."""
        params = ReadParams.model_validate(invocation.params)
        path = Path(params.path)

        if not path.exists():
            return ToolResult.error(invocation.id, f"File not found: {params.path}")
        if not path.is_file():
            return ToolResult.error(invocation.id, f"Not a file: {params.path}")

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot read file: {e}")

        total = len(lines)
        start = params.offset
        end = min(start + params.limit, total)
        chunk = lines[start:end]

        numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(chunk))

        footer = ""
        truncated = end < total
        if truncated:
            footer = (
                f"\n\n[Showing lines {start + 1}–{end} of {total}. Use offset={end} to read more.]"
            )

        metadata = {
            "file_path": str(path),
            "total_lines": total,
            "lines_returned": len(chunk),
            "offset": start,
            "truncated": truncated,
        }
        return ToolResult.ok(invocation.id, numbered + footer, metadata=metadata)
