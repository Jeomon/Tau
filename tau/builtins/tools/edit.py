from __future__ import annotations

import difflib
import re
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


def _render_edit_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("edit", args.get("path", ""))


_CONTEXT_LINES = 2


class EditParams(BaseModel):
    """Parameters for the edit tool."""

    path: str = Field(description="Absolute path to the file to edit.")
    old_string: str = Field(description="Exact string to find and replace.")
    new_string: str = Field(description="Replacement string.")
    replace_all: bool = Field(
        default=False, description="Replace all occurrences; default replaces only the first."
    )


def _parse_hunks(diff: str) -> list[list[tuple[str, int, int, str]]]:
    """Parse unified diff into hunks of (char, old_line, new_line, text)."""
    hunks: list[list[tuple[str, int, int, str]]] = []
    current: list[tuple[str, int, int, str]] = []
    old_line = new_line = 0
    for raw in diff.splitlines():
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                if current:
                    hunks.append(current)
                    current = []
                old_line, new_line = int(m.group(1)), int(m.group(2))
        elif raw.startswith("+"):
            current.append(("+", old_line, new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            current.append(("-", old_line, new_line, raw[1:]))
            old_line += 1
        else:
            current.append((" ", old_line, new_line, raw[1:]))
            old_line += 1
            new_line += 1
    if current:
        hunks.append(current)
    return hunks


def _render_edit_result(content: str, opts: Any) -> list[str]:
    from tau.tui.ansi import DIM, GREEN, RED, RESET

    metadata = opts.metadata or {}
    added = metadata.get("lines_added", 0)
    removed = metadata.get("lines_removed", 0)
    diff = metadata.get("diff", "")

    parts = []
    if added:
        parts.append(f"{GREEN}Added {added} {'line' if added == 1 else 'lines'}{RESET}")
    if removed:
        parts.append(f"{RED}Removed {removed} {'line' if removed == 1 else 'lines'}{RESET}")
    result = [", ".join(parts) if parts else content.strip()]

    if not diff:
        return result

    hunks = _parse_hunks(diff)
    if not hunks:
        return result

    has_gaps = False

    if opts.expanded:
        for hunk in hunks:
            for char, ol, nl, text in hunk:
                if char == "+":
                    result.append(f"{GREEN}{nl}  +  {text}{RESET}")
                elif char == "-":
                    result.append(f"{RED}{ol}  -  {text}{RESET}")
                else:
                    result.append(f"{nl}     {text}")
    else:
        prev_new_end: int | None = None
        for hunk in hunks:
            new_start = hunk[0][2]
            if prev_new_end is not None:
                gap = new_start - prev_new_end
                if gap > 0:
                    result.append(f"{DIM}  ···  {gap} line{'s' if gap != 1 else ''}{RESET}")
                    has_gaps = True

            changed = {i for i, (c, *_) in enumerate(hunk) if c != " "}
            show = {
                j
                for ci in changed
                for j in range(max(0, ci - _CONTEXT_LINES), min(len(hunk), ci + _CONTEXT_LINES + 1))
            }

            prev_i: int | None = None
            for i, (char, ol, nl, text) in enumerate(hunk):
                if i not in show:
                    continue
                if prev_i is not None and i > prev_i + 1:
                    gap = i - prev_i - 1
                    result.append(f"{DIM}  ···  {gap} line{'s' if gap != 1 else ''}{RESET}")
                    has_gaps = True
                if char == "+":
                    result.append(f"{GREEN}{nl}  +  {text}{RESET}")
                elif char == "-":
                    result.append(f"{RED}{ol}  -  {text}{RESET}")
                else:
                    result.append(f"{nl}     {text}")
                prev_i = i

            last = hunk[-1]
            prev_new_end = last[2] + (1 if last[0] != "-" else 0)

    if has_gaps or opts.expanded:
        hint = "(ctrl+o to collapse)" if opts.expanded else "···  (ctrl+o to expand)"
        result.append(f"{DIM}  {hint}{RESET}")

    return result


class EditTool(Tool):
    """Tool for replacing exact strings in files."""

    def __init__(self) -> None:
        super().__init__(
            name="edit",
            description=(
                "Replace an exact string in a file. Fails if old_string is not found or"
                " (when replace_all=false) appears more than once."
                " Use replace_all=true to replace every occurrence."
            ),
            schema=EditParams,
            kind=ToolKind.Edit,
            render_result=_render_edit_result,
            render_call=_render_edit_call,
            render_shell="default",
            prompt_guidelines=(
                "Read the file first so you understand context."
                " Prefer small, targeted edits over rewriting large sections."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        return args.get("path", "edit")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = EditParams.model_validate(invocation.params)
        path = Path(params.path)

        if not path.exists():
            return ToolResult.error(invocation.id, f"File not found: {params.path}")
        if not path.is_file():
            return ToolResult.error(invocation.id, f"Not a file: {params.path}")

        try:
            original = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot read file: {e}")

        count = original.count(params.old_string)
        if count == 0:
            return ToolResult.error(invocation.id, f"old_string not found in {params.path}")
        if not params.replace_all and count > 1:
            return ToolResult.error(
                invocation.id,
                f"old_string matches {count} locations in {params.path}. "
                "Provide more context to make it unique, or set replace_all=true.",
            )

        if params.replace_all:
            updated = original.replace(params.old_string, params.new_string)
        else:
            updated = original.replace(params.old_string, params.new_string, 1)

        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot write file: {e}")

        replacements = count if params.replace_all else 1

        original_lines = original.splitlines(keepends=True)
        updated_lines = updated.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                original_lines,
                updated_lines,
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
                n=99999,
            )
        )
        diff = "".join(diff_lines)
        lines_added = sum(
            1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
        )
        lines_removed = sum(
            1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
        )

        metadata = {
            "file_path": str(path),
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "diff": diff,
            "occurrences_replaced": replacements,
            "replace_all": params.replace_all,
            "total_lines": len(updated_lines),
        }
        return ToolResult.ok(
            invocation.id,
            f"Replaced {replacements} occurrence(s) in {params.path}",
            metadata=metadata,
        )
