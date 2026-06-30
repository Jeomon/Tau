from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.builtins.tools.utils import human_size
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


def _render_ls_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("ls", args.get("path", ""))


class LsParams(BaseModel):
    """Parameters for the ls tool."""

    path: str = Field(
        default="",
        description=(
            "Directory to list. An empty value uses the agent's working directory; a relative "
            "value is resolved from Tau's process working directory."
        ),
        examples=["/home/user/project", "/home/user/project/src"],
    )


def _render_ls_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import DIM, RESET

    metadata = opts.metadata or {}
    path = metadata.get("path", "")
    file_count = metadata.get("file_count", 0)
    dir_count = metadata.get("dir_count", 0)
    entries = metadata.get("entries", [])

    parts = []
    if dir_count:
        parts.append(f"{dir_count} {'dir' if dir_count == 1 else 'dirs'}")
    if file_count:
        parts.append(f"{file_count} {'file' if file_count == 1 else 'files'}")
    summary = f"Found {', '.join(parts)}" if parts else (path or "empty directory")
    result = [summary]

    if not entries:
        return result

    for entry in entries:
        name = entry["name"]
        is_dir = entry["is_dir"]
        size_str = entry.get("size_str", "")
        if is_dir:
            result.append(f"{name}/")
        else:
            tail = f"  {DIM}{size_str}{RESET}" if size_str else ""
            result.append(f"{name}{tail}")

    return result


class LsTool(Tool):
    """Tool for listing directory contents."""

    def __init__(self) -> None:
        super().__init__(
            name="ls",
            description=(
                "List a directory's immediate files and subdirectories without recursing."
            ),
            schema=LsParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_ls_result,
            render_call=_render_ls_call,
            render_shell="default",
            prompt_guidelines=(
                "Use to get an overview of a directory before diving into files."
                " Use glob for targeted file discovery."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        return args.get("path", ".") or "."

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = LsParams.model_validate(invocation.params)
        target = Path(params.path or invocation.cwd or ".").resolve()

        if not target.exists():
            return ToolResult.error(invocation.id, f"Path not found: {target}")
        if not target.is_dir():
            return ToolResult.error(invocation.id, f"Not a directory: {target}")

        try:
            raw_entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return ToolResult.error(invocation.id, f"Permission denied: {target}")

        file_count = dir_count = 0
        entries = []
        lines = [f"{target}/"]
        for entry in raw_entries:
            is_dir = entry.is_dir()
            if is_dir:
                dir_count += 1
            else:
                file_count += 1
            try:
                size_str = human_size(entry.stat().st_size) if entry.is_file() else ""
            except OSError:
                size_str = ""
            entries.append({"name": entry.name, "is_dir": is_dir, "size_str": size_str})
            suffix = "/" if is_dir else ""
            lines.append(f"  {entry.name}{suffix}{f'  {size_str}' if size_str else ''}")

        metadata = {
            "path": str(target),
            "file_count": file_count,
            "dir_count": dir_count,
            "entries": entries,
        }
        return ToolResult.ok(invocation.id, "\n".join(lines), metadata=metadata)
