from __future__ import annotations

import hashlib
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

    path: str = Field(
        description="Absolute filesystem path to the file to be read.",
        examples=["/home/user/project/src/main.py", "/home/user/project/README.md"],
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Line number to start reading from (0-based).",
        examples=[0, 100, 250],
    )
    limit: int = Field(
        default=2000,
        ge=1,
        description="Maximum number of lines to read.",
        examples=[50, 100, 2000],
    )


def _render_read_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import DIM, RESET

    metadata = opts.metadata or {}
    lines_returned = metadata.get("lines_returned", 0)

    line_word = "line" if lines_returned == 1 else "lines"
    result = [f"Read {lines_returned} {line_word}"]

    parsed = []
    for raw in content.splitlines():
        if "|" in raw:
            anchor, _, text = raw.partition("|")
            parsed.append((anchor, text))

    if not parsed:
        return result

    for num, text in parsed:
        result.append(f"{DIM}{num}{RESET}  {text}")

    return result


class ReadTool(Tool):
    """Tool for reading file contents with hashline anchors."""

    def __init__(self) -> None:
        super().__init__(
            name="read",
            description=(
                "Read the contents of a file. Returns each line with a stable hashline"
                " anchor in the format '<line>:<hash>|<content>'."
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

        def line_hash(txt: str) -> str:
            stripped = txt.strip()
            return "    " if not stripped else hashlib.md5(stripped.encode()).hexdigest()[:4]

        numbered = "\n".join(
            f"{start + i + 1}:{line_hash(line)}|{line}" for i, line in enumerate(chunk)
        )

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
