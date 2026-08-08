from __future__ import annotations

import os
import re
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

_DEFAULT_LIMIT = 500
_MAX_LIMIT = 2000
_MAX_CONTEXT = 10
# ripgrep is expected to be fast; unlike the terminal tool (which runs
# arbitrary, legitimately long-running commands and lets the agent set its
# own timeout), a search taking this long means something's wrong — a huge
# or network-mounted tree, rg stuck on a special file — not intentional work.
_TIMEOUT_SECONDS = 30.0

# One ripgrep result line: `<path>:<lineno>:<text>`.
#
# Both ends can contain colons — a Windows path starts `C:\`, and matched code
# is full of `key: value` — so neither a left nor a right split is safe. The
# line number is the only unambiguous anchor: `.*?` stays as short as possible
# but is forced to grow past a drive letter, because what follows the colon it
# stops at must be digits and then another colon. `.*` then takes the rest of
# the text verbatim, colons included.
_MATCH_LINE = re.compile(r"^(.*?):(\d+):(.*)$")
# With --context, ripgrep marks the surrounding lines with dashes instead of
# colons (`<path>-<lineno>-<text>`) and separates non-adjacent groups with a
# bare `--`. Same anchoring logic as above, with the roles of the separators
# swapped.
_CONTEXT_LINE = re.compile(r"^(.*?)-(\d+)-(.*)$")
_GROUP_SEPARATOR = "--"


def _absolutize(line: str, base: Path) -> str:
    """Re-attach ``base`` to a match line produced with "." as the search root.

    Only the leading "./" (or ".\\" on Windows) is replaced, so the rest of the
    line — ``:<lineno>:<text>``, where the text may itself contain colons — is
    left exactly as ripgrep emitted it.
    """
    rel = line[2:] if line.startswith(("./", ".\\")) else line
    return f"{base}{os.sep}{rel}"


def _apply_limit(lines: list[str], limit: int, context: int) -> tuple[list[str], int, bool]:
    """Trim ripgrep output to at most ``limit`` matching lines.

    Returns the kept lines, how many of them are matches, and whether anything
    was dropped. With ``--context`` the output interleaves matches, context
    lines and ``--`` separators, so the cut is made on match count rather than
    line count.

    A context run between two matches belongs to both — trailing context for
    the earlier, leading context for the later — so once the later one is
    dropped, only ``context`` lines of that run are still earned. Cutting the
    whole run would strip the kept match of its own trailing context; keeping
    it whole would leave lines hanging under no match at all.
    """
    kept: list[str] = []
    matches = 0
    for line in lines:
        if _MATCH_LINE.match(line):
            if matches == limit:
                break
            matches += 1
        kept.append(line)
    else:
        return kept, matches, False
    last_match = max(i for i, line in enumerate(kept) if _MATCH_LINE.match(line))
    del kept[last_match + 1 + context :]
    if kept and kept[-1] == _GROUP_SEPARATOR:
        kept.pop()
    return kept, matches, True


def _render_grep_call(args: dict, _streaming: bool) -> list[str]:
    query = args.get("pattern", "")
    query = " ".join(query.split())
    path = args.get("path", "")
    return call_line("grep", query, path)


def _render_grep_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import DIM, RESET

    if opts.is_error:
        return content.splitlines() or [content]

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

    result = [summary]
    for line in content.splitlines():
        match = _MATCH_LINE.match(line)
        if match is not None:
            path, lineno, text = match.groups()
            result.append(f"{DIM}{path}:{lineno}{RESET}  {text}")
            continue
        if line == _GROUP_SEPARATOR:
            result.append(f"{DIM}{_GROUP_SEPARATOR}{RESET}")
            continue
        context = _CONTEXT_LINE.match(line)
        if context is not None:
            path, lineno, text = context.groups()
            # Dim the body as well as the location: the whole line is there for
            # orientation, and the match lines have to stay the eye's anchor.
            result.append(f"{DIM}{path}:{lineno}{RESET}  {DIM}{text}{RESET}")
            continue
        # Anything else — today only the truncation marker, which names the cap
        # the summary's "(truncated)" does not. The old filter dropped every
        # colon-less line, so that notice never reached the transcript at all.
        if line.strip():
            result.append(line)
    return result


class GrepParams(BaseModel):
    """Parameters for the grep tool."""

    pattern: str = Field(
        default="",
        description="Regular expression to search for.",
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
        description="Glob pattern to filter files (e.g. '*.py').",
        examples=["*.py", "*.ts", "*.{ts,tsx}"],
    )
    case_sensitive: bool = Field(
        default=True,
        description="Whether the pattern is case-sensitive.",
        examples=[True, False],
    )
    literal: bool = Field(
        default=False,
        description=(
            "Treat the pattern as a literal string instead of a regex. Use for text "
            "full of regex metacharacters, e.g. 'foo(bar)' or 'a.b[0]'."
        ),
        examples=[True, False],
    )
    context: int = Field(
        default=0,
        ge=0,
        le=_MAX_CONTEXT,
        description=(
            "Lines of surrounding context to show around each match. Context lines are "
            "returned as 'file-line- content' (dashes) to distinguish them from matches."
        ),
        examples=[0, 2],
    )
    limit: int = Field(
        default=_DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description=f"Maximum matching lines to return (default {_DEFAULT_LIMIT}).",
        examples=[20, 500],
    )


class GrepTool(Tool):
    """Tool for searching files by regex pattern."""

    def __init__(self) -> None:
        super().__init__(
            name="grep",
            description=(
                "Search for a regex pattern in files. Returns matches as 'file:line: content', "
                f"up to 'limit' of them (default {_DEFAULT_LIMIT}). Directory searches are "
                "recursive and use ripgrep's default filtering, which excludes hidden and "
                "ignored files. Pattern syntax is Rust regex (RE2-style): alternation is "
                "'foo|bar', not 'foo\\|bar', and lookaround/backreferences are not supported "
                "— pass literal=true to search for the pattern verbatim instead."
            ),
            schema=GrepParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_grep_result,
            render_call=_render_grep_call,
            render_shell="default",
            prompt_guidelines=(
                "Prefer over read when searching for a symbol, function, or pattern across "
                "the codebase. Use the default regex mode. Use this tool instead of "
                "grep/rg/ag/git grep via terminal, even for a single match."
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
        if not params.pattern:
            return ToolResult.error(invocation.id, "Provide a 'pattern' to search for.")
        result = await self._rg(params, target, signal)
        if result.get("error"):
            return ToolResult.error(invocation.id, result["output"])
        if result["matches"]:
            return ToolResult.ok(invocation.id, result["output"], metadata=result["metadata"])
        return ToolResult.ok(
            invocation.id,
            f"No matches for pattern: {params.pattern}",
            metadata=result["metadata"],
        )

    async def _rg(self, params: GrepParams, target: Path, signal: AbortSignal | None) -> dict:
        # Same anchoring problem as glob.py's _rg_files: ripgrep matches a glob
        # containing "/" against the entire path it walks, so an `include` like
        # "src/**/*.py" matches nothing when the search root is absolute.
        # Anchoring the glob instead (--glob /abs/base/src/**/*.py) does not
        # work — ripgrep does not match absolute globs. Rooting the walk at "."
        # with cwd=base is what makes `include` relative to the search path.
        if target.is_dir():
            root, cwd, base = ".", target, target
        else:
            # A single file can be searched directly. ripgrep does not apply
            # --glob to explicitly-passed files, so `include` is a no-op here.
            root, cwd, base = target.name, target.parent, target.parent
        cmd = ["rg", "--line-number", "--no-heading", "--with-filename"]
        if not params.case_sensitive:
            cmd.append("--ignore-case")
        if params.literal:
            cmd.append("--fixed-strings")
        if params.context:
            cmd += ["--context", str(params.context)]
        if params.include:
            cmd += ["--glob", params.include]
        cmd += [params.pattern, root]
        # With context, rg emits up to 2*context extra lines per match plus a
        # `--` between non-adjacent groups, so the subprocess budget has to
        # cover those before `limit` matches can possibly be reached.
        budget = params.limit * (2 * params.context + 2) if params.context else params.limit
        try:
            returncode, lines, cancelled, timed_out = await run_bounded_lines(
                cmd, max_lines=budget, signal=signal, timeout=_TIMEOUT_SECONDS, cwd=cwd
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
        if timed_out:
            return {
                "matches": [],
                "output": f"Search timed out after {_TIMEOUT_SECONDS:.0f}s.",
                "metadata": {},
                "error": True,
            }
        if returncode not in (0, 1) and len(lines) <= budget:
            error = "\n".join(lines).strip() or f"ripgrep exited with status {returncode}."
            return {
                "matches": [],
                "output": error,
                "metadata": {},
                "error": True,
            }
        # The subprocess budget can bite before `limit` matches are reached
        # when context lines are in play, so neither signal alone is enough.
        budget_hit = len(lines) > budget
        lines, match_count, cut = _apply_limit(lines, params.limit, params.context)
        truncated = cut or budget_hit
        # Count distinct files while the paths are still relative: splitting an
        # absolute Windows path on ":" yields the drive letter, not the file.
        files_with_matches = len({m.group(1) for ln in lines if (m := _MATCH_LINE.match(ln))})
        # Callers expect fully resolved paths, and `base` is already resolved.
        lines = [ln if ln == _GROUP_SEPARATOR else _absolutize(ln, base) for ln in lines]
        metadata = {
            "pattern": params.pattern,
            "files_searched": files_with_matches,
            "match_count": match_count,
            "truncated": truncated,
        }
        output = "\n".join(lines)
        if truncated:
            # Report what was kept rather than the cap: with context the read
            # budget can stop the search short of `limit`, and naming the cap
            # would then claim matches that were never counted.
            output += f"\n\n[Results truncated: showing {match_count} matches.]"
        return {"matches": lines, "output": output, "metadata": metadata}
