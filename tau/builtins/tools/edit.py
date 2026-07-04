from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.builtins.tools.hashline import compute_line_hashes
from tau.builtins.tools.utils import atomic_write_text, serialize_file_mutation
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
from tau.tui.style import apply_style


def _render_edit_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("edit", args.get("path", ""))


class EditParams(BaseModel):
    """Parameters for the edit tool."""

    path: str = Field(
        description="Absolute path to the file to edit.",
        examples=["/home/user/project/src/main.py", "/home/user/project/config.json"],
    )
    start_anchor: str = Field(
        pattern=r"^\d+:.{4}$",
        description=(
            "Hashline anchor copied from read for the first line to replace, formatted "
            "'<line>:<hash>'."
        ),
        examples=["12:a3f1"],
    )
    end_anchor: str = Field(
        pattern=r"^\d+:.{4}$",
        description=(
            "Hashline anchor for the last line to replace, formatted '<line>:<hash>'. "
            "Use the start anchor for a single-line edit."
        ),
        examples=["14:9c8a"],
    )
    new_content: str = Field(
        description=(
            "UTF-8 text replacing the inclusive anchored line range. Use an empty string "
            "to delete the range."
        ),
        examples=["def new_function():\n    return 5"],
    )


def _line_hash(line: str) -> str:
    """Return an isolated per-line hash for cosmetic diff-preview display only.

    Used solely by _render_hunk_line (the human-facing TUI diff panel), which
    only has the changed lines in front of it, not the whole file — so it
    can't run the collision-resolved compute_line_hashes over full context.
    Anchor *resolution* (_find_anchor) never uses this; it always hashes the
    complete file so identical/blank lines still get distinct anchors.
    """
    stripped = line.strip()
    return "    " if not stripped else hashlib.md5(stripped.encode()).hexdigest()[:4]


def _parse_anchor(anchor: str) -> tuple[int, str]:
    """Parse a validated hashline anchor into its line hint and hash."""
    line_number, line_hash = anchor.split(":", 1)
    return int(line_number), line_hash


def _find_anchor(lines: list[str], anchor: str, hashes: list[str] | None = None) -> int | None:
    """Find the line matching the anchor's hash.

    ``hashes`` (from ``compute_line_hashes``) are unique per file, so a match
    is normally unambiguous — the line-number hint is only used to break a
    tie in the pathological case where the file is long enough to exhaust the
    collision-resolution retry budget and two lines end up sharing a hash.
    """
    line_hint, expected_hash = _parse_anchor(anchor)
    if hashes is None:
        hashes = compute_line_hashes(lines)
    matches = [index for index, h in enumerate(hashes) if h == expected_hash]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    expected_index = line_hint - 1
    return min(matches, key=lambda index: abs(index - expected_index))


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


def _render_hunk_line(char: str, old_line: int, new_line: int, text: str) -> str:
    """Render a diff line with the hashline anchor for that version."""
    line_number = old_line if char == "-" else new_line
    anchor = f"{line_number}:{_line_hash(text)}"
    marker = f"  {char}  " if char != " " else "     "
    return f"{anchor}{marker}{text}"


def _collapse_hunk_context(
    hunk: list[tuple[str, int, int, str]],
    context_lines: int = 3,
) -> tuple[list[tuple[str, int, int, str] | str], int]:
    """Keep changes and nearby context, replacing unchanged gaps with markers."""
    changed = [index for index, (char, *_rest) in enumerate(hunk) if char != " "]
    if not changed:
        return list(hunk), 0

    visible: set[int] = set()
    for index in changed:
        start = max(0, index - context_lines)
        end = min(len(hunk), index + context_lines + 1)
        visible.update(range(start, end))

    collapsed: list[tuple[str, int, int, str] | str] = []
    hidden_total = 0
    index = 0
    while index < len(hunk):
        if index in visible:
            collapsed.append(hunk[index])
            index += 1
            continue
        gap_start = index
        while index < len(hunk) and index not in visible:
            index += 1
        hidden = index - gap_start
        hidden_total += hidden
        collapsed.append(f"… (+{hidden} {'line' if hidden == 1 else 'lines'})")
    return collapsed, hidden_total


def _render_edit_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import GREEN, RED, RESET

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

    hidden_total = 0
    for hunk in hunks:
        displayed: list[tuple[str, int, int, str] | str]
        if opts.expanded:
            displayed = list(hunk)
        else:
            displayed, hidden = _collapse_hunk_context(hunk)
            hidden_total += hidden
        for line in displayed:
            if isinstance(line, str):
                muted = apply_style(opts.theme.dim, line) if opts.theme is not None else line
                result.append(muted)
                continue
            char, ol, nl, text = line
            if char == "+":
                result.append(f"{GREEN}{_render_hunk_line(char, ol, nl, text)}{RESET}")
            elif char == "-":
                result.append(f"{RED}{_render_hunk_line(char, ol, nl, text)}{RESET}")
            else:
                result.append(_render_hunk_line(char, ol, nl, text))

    if hidden_total:
        result.append("(ctrl+o to expand)")
    elif opts.expanded and any(_collapse_hunk_context(hunk)[1] for hunk in hunks):
        result.append("(ctrl+o to collapse)")

    return result


_ANCHOR_FORMAT_HINT = (
    "The 'edit' tool takes content-based hashline anchors, not line numbers. Call 'read' "
    "on this file first, then copy the '<line>:<hash>' anchor from its output for the "
    "first and last line to replace — e.g. start_anchor=\"12:a3f1\", end_anchor=\"14:9c8a\". "
    "Use the same anchor for both fields on a single-line edit."
)


class EditTool(Tool):
    """Tool for replacing line ranges selected by hashline anchors."""

    def __init__(self) -> None:
        super().__init__(
            name="edit",
            description=(
                "Replace an inclusive line range using content-based hashline anchors from "
                "read. Every line has a distinct anchor, so an anchor always resolves to "
                "exactly the line it was copied from, even across repeated or blank lines — "
                "it can survive shifted surrounding lines since it's not based on line "
                "number. Rewriting may normalize line endings throughout the file."
            ),
            schema=EditParams,
            kind=ToolKind.Edit,
            render_result=_render_edit_result,
            render_call=_render_edit_call,
            render_shell="default",
            result_expandable=False,
            prompt_guidelines=(
                "Read the file first and copy its hashline anchors exactly."
                " Use the same start and end anchor for a single-line edit."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        return args.get("path", "edit")

    def validate(self, params: dict[str, Any]) -> tuple[bool, list[str]]:
        """Layer an actionable hint onto anchor-related schema errors.

        The bare Pydantic error ("start_anchor: Field required") never tells the
        model what a valid anchor looks like or that it needs to call ``read``
        first — observed in practice to make the model repeat the exact same
        wrong shape (e.g. line_start/line_end) across several consecutive
        calls instead of self-correcting. Appending a concrete example fixes
        that without changing validation semantics.
        """
        ok, errors = super().validate(params)
        if not ok and any("start_anchor" in e or "end_anchor" in e for e in errors):
            errors.append(_ANCHOR_FORMAT_HINT)
        return ok, errors

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = EditParams.model_validate(invocation.params)
        path = Path(params.path)
        async with serialize_file_mutation(path):
            return self._edit(invocation, params, path)

    def _edit(self, invocation: ToolInvocation, params: EditParams, path: Path) -> ToolResult:
        if not path.exists():
            return ToolResult.error(invocation.id, f"File not found: {params.path}")
        if not path.is_file():
            return ToolResult.error(invocation.id, f"Not a file: {params.path}")

        try:
            original = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot read file: {e}")

        lines = original.splitlines()
        hashes = compute_line_hashes(lines)
        start_index = _find_anchor(lines, params.start_anchor, hashes)
        if start_index is None:
            return ToolResult.error(
                invocation.id,
                f"Start anchor hash not found: {params.start_anchor}",
            )
        end_index = _find_anchor(lines, params.end_anchor, hashes)
        if end_index is None:
            return ToolResult.error(
                invocation.id,
                f"End anchor hash not found: {params.end_anchor}",
            )
        if end_index < start_index:
            return ToolResult.error(
                invocation.id,
                "Resolved end anchor is before the start anchor.",
            )

        replacement_lines = params.new_content.splitlines()
        updated_lines = lines[:start_index] + replacement_lines + lines[end_index + 1 :]
        updated = "\n".join(updated_lines)
        if original.endswith("\n") and updated_lines:
            updated += "\n"
        replacements = end_index - start_index + 1

        try:
            atomic_write_text(path, updated)
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot write file: {e}")

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
            "start_anchor": params.start_anchor,
            "end_anchor": params.end_anchor,
            "total_lines": len(updated_lines),
        }
        return ToolResult.ok(
            invocation.id,
            f"Replaced {replacements} occurrence(s) in {params.path}",
            metadata=metadata,
        )
