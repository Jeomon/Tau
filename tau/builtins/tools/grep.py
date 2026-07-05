from __future__ import annotations

import json
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


def _render_grep_call(args: dict, _streaming: bool) -> list[str]:
    query = args.get("rule") or args.get("pattern", "")
    query = " ".join(query.split())
    path = args.get("path", "")
    return call_line("grep", query, path)


_MAX_MATCHES = 500


def _render_grep_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import DIM, RESET

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

    for line in lines:
        file_part, _, rest = line.partition(":")
        lineno, _, text = rest.partition(": ")
        result.append(f"{DIM}{file_part}:{lineno.strip()}{RESET}  {text}")

    return result


class GrepParams(BaseModel):
    """Parameters for the grep tool."""

    pattern: str = Field(
        default="",
        description=(
            "Regular expression to search for. If ast is true, this is instead an ast-grep "
            "structural pattern with $VAR-style meta-variables (e.g. '$A && $A()'), not a "
            "regex. Required unless rule is set."
        ),
        examples=["def parse_config", "class UserService", "TODO|FIXME"],
    )
    path: str = Field(
        default="",
        description=(
            "File or directory to search. An empty value uses the agent's working directory; "
            "a relative value is resolved from Tau's process working directory."
        ),
        examples=["/home/user/project/src", "/home/user/project/src/main.py"],
    )
    include: str = Field(
        default="",
        description=(
            "Glob pattern to filter files (e.g. '*.py'). Only used when path is a directory."
        ),
        examples=["*.py", "*.ts", "*.{ts,tsx}"],
    )
    case_sensitive: bool = Field(
        default=True,
        description="Whether the pattern is case-sensitive. Ignored when ast is true.",
        examples=[True, False],
    )
    ast: bool = Field(
        default=False,
        description=(
            "Use ast-grep for structural, AST-aware matching instead of ripgrep regex — "
            "finds code shapes regardless of formatting or naming. Language is inferred "
            "per-file from its extension."
        ),
        examples=[True, False],
    )
    rule: str = Field(
        default="",
        description=(
            "An ast-grep YAML rule (run as ast-grep scan --inline-rules) for structural "
            "queries a single pattern can't express: relational (has, inside), composite "
            "(all, any, not), or kind-based matching. Only used when ast is true, and takes "
            "precedence over pattern. Must include a top-level 'language' key."
        ),
        examples=[
            "language: python\nrule:\n  kind: class_definition",
            (
                "language: javascript\nrule:\n  pattern: console.log($$$)\n  inside:\n"
                "    kind: method_definition\n    stopBy: end"
            ),
        ],
    )


class GrepTool(Tool):
    """Tool for searching files by regex pattern."""

    def __init__(self) -> None:
        super().__init__(
            name="grep",
            description=(
                "Search for a regex pattern in files. Returns matches as 'file:line: content', "
                f"up to {_MAX_MATCHES} matches. Directory searches are recursive and use "
                "ripgrep's default filtering, which excludes hidden and ignored files. Set "
                "ast=true for structural AST matching with ast-grep instead."
            ),
            schema=GrepParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_grep_result,
            render_call=_render_grep_call,
            render_shell="default",
            prompt_guidelines=(
                "Prefer over read when searching for a symbol, function, or pattern across "
                "the codebase. Use ast=true with an ast-grep structural pattern (e.g. "
                "'$A && $A()') when searching for a code structure that may vary in "
                "formatting or naming; otherwise use the default regex mode. Metavariables "
                "must be uppercase ($ARG, not $arg) and must be the entire content of their "
                "AST node (no partial substitution like 'obj.on$EVENT'). For compound "
                "statements (for/if/while/def/etc.) include the body as '$$$BODY', e.g. "
                "'for $ITEM in $LIST:\\n    $$$BODY' — omitting it makes the pattern fail to "
                "parse and silently match nothing. For relational (has/inside), composite "
                "(all/any/not), or kind-based queries a single pattern can't express, use "
                "rule instead of pattern. If an ast search unexpectedly finds nothing, don't "
                "assume the code isn't there: run `ast-grep run --pattern '<pattern>' --lang "
                "<lang> --debug-query=pattern` via the shell tool to see how ast-grep parsed "
                "it, and adjust."
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
        params = GrepParams.model_validate(invocation.params)
        target = Path(params.path or invocation.cwd or ".").resolve()
        if not target.exists():
            return ToolResult.error(invocation.id, f"Path not found: {target}")
        if not params.pattern and not (params.ast and params.rule):
            return ToolResult.error(
                invocation.id, "Provide 'pattern' (or 'rule' when ast is true)."
            )

        if params.ast and params.rule:
            result = await self._ast_grep_scan(params, target, signal)
        elif params.ast:
            result = await self._ast_grep(params, target, signal)
        else:
            result = await self._rg(params, target, signal)
        if result.get("error"):
            return ToolResult.error(invocation.id, result["output"])
        if result["matches"]:
            return ToolResult.ok(invocation.id, result["output"], metadata=result["metadata"])
        query = params.rule if (params.ast and params.rule) else params.pattern
        return ToolResult.ok(
            invocation.id,
            f"No matches for pattern: {query}",
            metadata=result["metadata"],
        )

    async def _rg(self, params: GrepParams, target: Path, signal: AbortSignal | None) -> dict:
        cmd = ["rg", "--line-number", "--no-heading", "--with-filename"]
        if not params.case_sensitive:
            cmd.append("--ignore-case")
        if params.include:
            cmd += ["--glob", params.include]
        cmd += [params.pattern, str(target)]
        try:
            returncode, lines, cancelled = await run_bounded_lines(
                cmd, max_lines=_MAX_MATCHES, signal=signal
            )
        except FileNotFoundError:
            return {
                "matches": [],
                "output": "ripgrep (rg) is required but was not found.",
                "metadata": {},
                "error": True,
            }
        if cancelled:
            return {"matches": [], "output": "Search cancelled.", "metadata": {}, "error": True}
        if returncode not in (0, 1) and len(lines) <= _MAX_MATCHES:
            error = "\n".join(lines).strip() or f"ripgrep exited with status {returncode}."
            return {
                "matches": [],
                "output": error,
                "metadata": {},
                "error": True,
            }
        truncated = len(lines) > _MAX_MATCHES
        lines = lines[:_MAX_MATCHES]
        files_with_matches = len({ln.split(":")[0] for ln in lines})
        metadata = {
            "pattern": params.pattern,
            "files_searched": files_with_matches,
            "match_count": len(lines),
            "truncated": truncated,
        }
        output = "\n".join(lines)
        if truncated:
            output += f"\n\n[Results truncated at {_MAX_MATCHES} matches.]"
        return {"matches": lines, "output": output, "metadata": metadata}

    async def _ast_grep(self, params: GrepParams, target: Path, signal: AbortSignal | None) -> dict:
        cmd = ["ast-grep", "run", "--pattern", params.pattern, "--json=stream"]
        if params.include:
            cmd += ["--globs", params.include]
        cmd += [str(target)]
        return await self._run_ast_grep(cmd, params.pattern, signal)

    async def _ast_grep_scan(
        self, params: GrepParams, target: Path, signal: AbortSignal | None
    ) -> dict:
        cmd = ["ast-grep", "scan", "--inline-rules", params.rule, "--json=stream"]
        if params.include:
            cmd += ["--globs", params.include]
        cmd += [str(target)]
        return await self._run_ast_grep(cmd, params.rule, signal)

    async def _run_ast_grep(self, cmd: list[str], query: str, signal: AbortSignal | None) -> dict:
        try:
            returncode, lines, cancelled = await run_bounded_lines(
                cmd, max_lines=_MAX_MATCHES, signal=signal
            )
        except FileNotFoundError:
            return {
                "matches": [],
                "output": "ast-grep is required but was not found.",
                "metadata": {},
                "error": True,
            }
        if cancelled:
            return {"matches": [], "output": "Search cancelled.", "metadata": {}, "error": True}
        if returncode not in (0, 1) and len(lines) <= _MAX_MATCHES:
            error = "\n".join(lines).strip() or f"ast-grep exited with status {returncode}."
            return {
                "matches": [],
                "output": error,
                "metadata": {},
                "error": True,
            }

        truncated = len(lines) > _MAX_MATCHES
        lines = lines[:_MAX_MATCHES]
        formatted: list[str] = []
        files_seen: set[str] = set()
        for line in lines:
            try:
                match = json.loads(line)
            except json.JSONDecodeError:
                continue
            file_path = match.get("file", "")
            files_seen.add(file_path)
            line_no = match.get("range", {}).get("start", {}).get("line", 0) + 1
            text = match.get("lines", match.get("text", "")).strip()
            formatted.append(f"{file_path}:{line_no}: {text}")

        metadata = {
            "pattern": query,
            "files_searched": len(files_seen),
            "match_count": len(formatted),
            "truncated": truncated,
        }
        output = "\n".join(formatted)
        if truncated:
            output += f"\n\n[Results truncated at {_MAX_MATCHES} matches.]"
        return {"matches": formatted, "output": output, "metadata": metadata}
