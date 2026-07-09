from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field

from tau.builtins.tools.utils import (
    atomic_write_text,
    compute_line_hashes,
    resolve_tool_path,
    serialize_file_mutation,
)
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
    path: str = Field(
        description=(
            "Path to the file to edit. Prefer an absolute path; a relative value is "
            "resolved from the agent's working directory."
        ),
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
        validation_alias=AliasChoices("new_content", "content"),
        description=(
            "New UTF-8 content for the inclusive anchored line range. "
            "Empty content means delete the range."
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


def _format_anchored_lines(
    lines: list[str],
    hashes: list[str],
    start_index: int,
    end_index: int,
) -> str:
    """Format a current-file excerpt using read-compatible hashline anchors."""
    return "\n".join(
        f"{index + 1}:{hashes[index]}|{lines[index]}" for index in range(start_index, end_index)
    )


def _anchor_not_found_message(
    label: str,
    anchor: str,
    lines: list[str],
    hashes: list[str],
) -> str:
    """Build an actionable model-visible error for stale or invalid anchors."""
    line_hint, expected_hash = _parse_anchor(anchor)
    total_lines = len(lines)
    message = [
        f"{label} anchor hash not found: {anchor}",
        (
            f"No current line in the file has hash {expected_hash!r}. "
            "The file may have changed since the anchor was read, or the anchor "
            "may have been copied incorrectly."
        ),
    ]

    if total_lines == 0:
        message.append("The current file is empty. Re-read the file before retrying the edit.")
        return "\n".join(message)

    if 1 <= line_hint <= total_lines:
        start_index = max(0, line_hint - 3)
        end_index = min(total_lines, line_hint + 2)
        message.extend(
            [
                f"Current file content near hinted line {line_hint}:",
                _format_anchored_lines(lines, hashes, start_index, end_index),
            ]
        )
    else:
        message.append(
            f"The anchor line hint is {line_hint}, but the current file has {total_lines} lines."
        )

    message.append(
        "Re-read the relevant range with read and retry using the current hashline anchors."
    )
    return "\n".join(message)


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


_NOOP_ESCALATE_AFTER = 3


class EditTool(Tool):
    """Tool for replacing line ranges selected by hashline anchors."""

    def __init__(self) -> None:
        # Tracks consecutive byte-identical no-op edits per resolved path, so a
        # model stuck re-issuing the same already-applied (or misdiagnosed)
        # edit gets an escalating hard stop instead of silently "succeeding"
        # forever. Keyed by path; value is (payload_hash, consecutive_count).
        self._noop_state: dict[Path, tuple[str, int]] = {}
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
                " Re-read before editing again if the file may have changed since your"
                " last read — a formatter, hook, or another edit can shift anchors."
            ),
            strict="prefer",
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
        if ok:
            return ok, errors

        has_anchor_error = any(
            error.startswith(("start_anchor:", "end_anchor:"))
            or "start_anchor" in error
            or "end_anchor" in error
            for error in errors
        )
        has_legacy_line_params = "line_start" in params or "line_end" in params
        if has_anchor_error or has_legacy_line_params:
            errors.append(
                "Use hashline anchors from read, formatted like '12:a3f1'. "
                "Read the file first, then pass start_anchor/end_anchor; use the same "
                "anchor for a single-line edit."
            )
        return ok, errors

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = EditParams.model_validate(invocation.params)
        path = resolve_tool_path(params.path, invocation.cwd)
        async with serialize_file_mutation(path):
            return self._edit(invocation, params, path)

    def _handle_noop(
        self, invocation: ToolInvocation, params: EditParams, resolved: Path
    ) -> ToolResult:
        """Report an edit that parsed and resolved cleanly but changed nothing.

        Tracks consecutive identical (same anchors, same content) no-ops per
        file so a model stuck re-issuing an already-applied or misdiagnosed
        edit gets an escalating hard stop instead of quietly "succeeding"
        forever — mirrors the failure mode where a soft hint alone doesn't
        break the retry loop.
        """
        payload_hash = hashlib.md5(
            f"{params.start_anchor}:{params.end_anchor}:{params.new_content}".encode()
        ).hexdigest()
        previous_hash, previous_count = self._noop_state.get(resolved, ("", 0))
        count = previous_count + 1 if payload_hash == previous_hash else 1
        self._noop_state[resolved] = (payload_hash, count)

        if count >= _NOOP_ESCALATE_AFTER:
            return ToolResult.error(
                invocation.id,
                f"STOP. This exact edit to {params.path} has been a byte-identical no-op "
                f"{count} times in a row — the content is already present at the anchored "
                "range. Do not retry this same edit. Re-read the file to see the current "
                "state, or move on if the change is already there.",
            )
        return ToolResult.ok(
            invocation.id,
            f"Edit to {params.path} parsed and resolved cleanly, but produced no change: "
            "the given content is already present at the anchored range. Re-read the file "
            "before retrying — the anchors may be stale, or this change may already be applied.",
        )

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
                _anchor_not_found_message("Start", params.start_anchor, lines, hashes),
            )
        end_index = _find_anchor(lines, params.end_anchor, hashes)
        if end_index is None:
            return ToolResult.error(
                invocation.id,
                _anchor_not_found_message("End", params.end_anchor, lines, hashes),
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

        resolved = path.resolve()
        if updated == original:
            return self._handle_noop(invocation, params, resolved)
        self._noop_state.pop(resolved, None)

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
