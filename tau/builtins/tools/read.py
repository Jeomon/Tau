from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from tau.builtins.tools.utils import compute_line_hashes, looks_like_binary, resolve_tool_path
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


_MAX_LINE_CHARS = 4000


def _display_line(line: str) -> str:
    """Cap a single line's displayed length.

    Stops one pathologically long line (a minified bundle, a one-line JSON
    blob) from dumping megabytes into the model's context through a single
    hashline anchor. The anchor hash is computed over the full untruncated
    line elsewhere, so this is purely a display cap — it doesn't affect
    anchor resolution for a later edit.
    """
    if len(line) <= _MAX_LINE_CHARS:
        return line
    truncated = line[:_MAX_LINE_CHARS]
    suffix = f"…[line truncated for display at {_MAX_LINE_CHARS} chars; {len(line)} chars total]"
    return truncated + suffix


class ReadParams(BaseModel):
    """Parameters for the read tool."""

    path: str = Field(
        description=(
            "Path to the UTF-8 text file to read. Prefer an absolute path; a relative "
            "value is resolved from the agent's working directory."
        ),
        examples=["/home/user/project/src/main.py", "/home/user/project/README.md"],
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of lines to skip before reading (0 reads from the first line).",
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
                "Read a UTF-8 text file, replacing invalid byte sequences when decoding. "
                "Returns each line with a content-based hashline anchor in the format "
                "'<line>:<hash>|<content>'. Every line in the file gets a distinct anchor, "
                "including blank lines and repeated content. Use offset and limit to read "
                f"large files in chunks. A single line longer than {_MAX_LINE_CHARS} characters "
                "is truncated for display; refuses to read files that look like binary content."
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
        path = resolve_tool_path(params.path, invocation.cwd)

        if not path.exists():
            return ToolResult.error(invocation.id, f"File not found: {params.path}")
        if not path.is_file():
            return ToolResult.error(invocation.id, f"Not a file: {params.path}")

        try:
            raw = path.read_bytes()
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot read file: {e}")

        if looks_like_binary(raw):
            return ToolResult.error(
                invocation.id,
                f"'{params.path}' appears to be a binary file (contains a null byte in the "
                "first 8 KiB) and cannot be read as text.",
            )

        lines = raw.decode("utf-8", errors="replace").splitlines()

        total = len(lines)
        start = params.offset
        end = min(start + params.limit, total)
        chunk = lines[start:end]

        # Hashed over the whole file, not just this chunk, so collision
        # resolution (and therefore every line's anchor) stays identical
        # regardless of which offset/limit window is being displayed —
        # edit re-derives this same full-file table when resolving an anchor.
        chunk_hashes = compute_line_hashes(lines)[start:end]

        numbered = "\n".join(
            f"{start + i + 1}:{h}|{_display_line(line)}"
            for i, (h, line) in enumerate(zip(chunk_hashes, chunk, strict=True))
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
