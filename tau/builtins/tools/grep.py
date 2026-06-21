from __future__ import annotations

import re
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


def _render_grep_call(args: dict, _streaming: bool) -> list[str]:
    pattern = args.get("pattern", "")
    path = args.get("path", "")
    return call_line("grep", pattern, path)


_MAX_MATCHES = 500
_PREVIEW_LINES = 5


def _render_grep_result(content: str, opts: Any) -> list[str]:
    from tau.tui.ansi import DIM, RESET

    metadata = opts.metadata or {}
    match_count = metadata.get("match_count", 0)
    files_searched = metadata.get("files_searched", 0)
    truncated = metadata.get("truncated", False)

    if match_count == 0:
        return ["No matches found"]

    file_word = "file" if files_searched == 1 else "files"
    match_word = "match" if match_count == 1 else "matches"
    summary = f"Found {match_count} {match_word} in {files_searched} {file_word}"
    if truncated:
        summary += f"  {DIM}(truncated){RESET}"

    lines = [line for line in content.splitlines() if ":" in line]
    result = [summary]

    show = lines if opts.expanded else lines[:_PREVIEW_LINES]
    for line in show:
        file_part, _, rest = line.partition(":")
        lineno, _, text = rest.partition(": ")
        result.append(f"{DIM}{file_part}:{lineno.strip()}{RESET}  {text}")

    if opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  (ctrl+o to collapse){RESET}")
    elif not opts.expanded and len(lines) > _PREVIEW_LINES:
        result.append(f"{DIM}  ···  (ctrl+o to expand){RESET}")

    return result


class GrepParams(BaseModel):
    """Parameters for the grep tool."""

    pattern: str = Field(description="Regular expression to search for.")
    path: str = Field(
        default="", description="File or directory to search. Defaults to the agent's cwd."
    )
    include: str = Field(
        default="",
        description=(
            "Glob pattern to filter files (e.g. '*.py'). Only used when path is a directory."
        ),
    )
    case_sensitive: bool = Field(default=True, description="Whether the pattern is case-sensitive.")


class GrepTool(Tool):
    """Tool for searching files by regex pattern."""

    def __init__(self) -> None:
        super().__init__(
            name="grep",
            description=(
                "Search for a regex pattern in files. Returns matches as 'file:line: content', "
                f"up to {_MAX_MATCHES} matches. When path is a directory, searches recursively."
            ),
            schema=GrepParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_grep_result,
            render_call=_render_grep_call,
            render_shell="default",
            prompt_guidelines=(
                "Prefer over read when searching for a symbol, function,"
                " or pattern across the codebase."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Get a short display name for the grep operation."""
        return args.get("pattern", "grep")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the regex pattern search operation."""
        params = GrepParams.model_validate(invocation.params)

        flags = 0 if params.case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(params.pattern, flags)
        except re.error as e:
            return ToolResult.error(invocation.id, f"Invalid regex pattern: {e}")

        target = Path(params.path or invocation.cwd or ".").resolve()
        files: list[Path] = []

        if target.is_file():
            files = [target]
        elif target.is_dir():
            glob_pat = f"**/{params.include}" if params.include else "**/*"
            files = [p for p in target.glob(glob_pat) if p.is_file()]
        else:
            return ToolResult.error(invocation.id, f"Path not found: {target}")

        matches: list[str] = []
        truncated = False

        for file in sorted(files):
            if truncated:
                break
            try:
                for lineno, line in enumerate(
                    file.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if regex.search(line):
                        matches.append(f"{file}:{lineno}: {line}")
                        if len(matches) >= _MAX_MATCHES:
                            truncated = True
                            break
            except OSError:
                continue

        metadata = {
            "pattern": params.pattern,
            "files_searched": len(files),
            "match_count": len(matches),
            "truncated": truncated,
        }

        if not matches:
            return ToolResult.ok(
                invocation.id, f"No matches for pattern: {params.pattern}", metadata=metadata
            )

        result = "\n".join(matches)
        if truncated:
            result += f"\n\n[Results truncated at {_MAX_MATCHES} matches.]"

        return ToolResult.ok(invocation.id, result, metadata=metadata)
