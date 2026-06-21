from __future__ import annotations

from pathlib import Path
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


def _render_write_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("write", args.get("path", ""))


class WriteParams(BaseModel):
    """Parameters for the write tool."""

    path: str = Field(description="Absolute path to the file to write.")
    content: str = Field(description="Content to write to the file.")


_PREVIEW_LINES = 5


def _render_write_result(content: str, opts: Any) -> list[str]:
    from tau.tui.ansi import DIM, GREEN, RESET

    metadata = opts.metadata or {}
    total_lines = metadata.get("total_lines", 0)
    created = metadata.get("created", False)
    lines = metadata.get("lines", [])

    action = f"{GREEN}Created{RESET}" if created else "Written"
    line_word = "line" if total_lines == 1 else "lines"
    result = [f"{action} {total_lines} {line_word}"]

    if not lines:
        return result

    show = lines if opts.expanded else lines[:_PREVIEW_LINES]
    for i, text in enumerate(show, 1):
        result.append(f"{DIM}{i}{RESET}  {text}")

    if opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  (ctrl+o to collapse){RESET}")
    elif not opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  ···  (ctrl+o to expand){RESET}")

    return result


class WriteTool(Tool):
    """Tool for writing content to files."""

    def __init__(self) -> None:
        super().__init__(
            name="write",
            description=(
                "Write content to a file, creating it (and any missing parent directories)"
                " if needed. Overwrites the file if it already exists."
            ),
            schema=WriteParams,
            kind=ToolKind.Write,
            render_result=_render_write_result,
            render_call=_render_write_call,
            render_shell="default",
            prompt_guidelines=(
                "Only use for new files or complete rewrites. Use edit to modify existing files."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Get a short display name for the write operation."""
        return args.get("path", "write")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the file write operation."""
        params = WriteParams.model_validate(invocation.params)
        path = Path(params.path)

        created = not path.exists()

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot write file: {e}")

        bytes_written = len(params.content.encode("utf-8"))
        content_lines = params.content.splitlines()
        total_lines = len(content_lines)
        metadata = {
            "file_path": str(path),
            "total_lines": total_lines,
            "bytes_written": bytes_written,
            "created": created,
            "lines": content_lines,
        }
        return ToolResult.ok(
            invocation.id, f"Written {bytes_written} bytes to {params.path}", metadata=metadata
        )
