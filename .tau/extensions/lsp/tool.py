from __future__ import annotations

import json
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

from .core import LSP
from .helper import add_snippets as _add_snippets
from .helper import normalize as _normalize
from .types import NEEDS_NAME, NEEDS_RANGE, Operation

# Operations that apply edits to disk — skip normalization, they return structured summaries
_EDIT_OPS = frozenset({"rename", "codeAction", "formatting"})

# Operations that return raw server data — skip normalize/snippets
_RAW_OPS: frozenset[str] = frozenset()

# Cross-file operations that need the language server to finish indexing before querying
_INDEX_OPS = frozenset({"findReferences", "incomingCalls", "outgoingCalls", "workspaceSymbol"})

# Ops that resolve a symbol at line/character — an empty result usually means the
# position isn't on a symbol name, so we return positioning guidance instead of a
# bare "No results".
_POSITION_OPS = frozenset(
    {
        "goToDefinition",
        "goToImplementation",
        "findReferences",
        "incomingCalls",
        "outgoingCalls",
        "hover",
    }
)

# Call-hierarchy ops: most servers resolve these on a call site / symbol reference,
# not the symbol's own declaration — worth saying so explicitly.
_CALL_HIERARCHY_OPS = frozenset({"incomingCalls", "outgoingCalls"})

# Navigation ops get multi-line snippets (show the full definition body)
_NAV_OPS = frozenset({"goToDefinition", "goToImplementation"})

# Reference ops get single-line snippets (show the usage line only)
_SNIPPET_OPS = frozenset(
    {
        "findReferences",
        "incomingCalls",
        "outgoingCalls",
        "workspaceSymbol",
    }
)


def _render_lsp_call(args: dict, _streaming: bool) -> list[str]:
    op = args.get("operation", "")
    file_path = args.get("file_path", "")
    line = args.get("line", "")
    query = args.get("query", "")
    new_name = args.get("new_name", "")
    filename = Path(file_path).name if file_path else ""

    if op == "workspaceSymbol":
        return call_line("lsp", op, query)
    if op in ("documentSymbol", "formatting"):
        return call_line("lsp", op, filename)
    if op == "rename":
        loc = f"{filename}:{line}" if filename else ""
        return call_line("lsp", op, loc, new_name)
    loc = f"{filename}:{line}" if filename and line else filename
    return call_line("lsp", op, loc)


def _fmt_loc(loc: dict) -> str:
    # loc is already normalized: 'path' key (relative), 1-based line numbers
    path = loc.get("path", "")
    name = Path(path).name if path else "?"
    start = loc.get("range", {}).get("start", {})
    line = start.get("line", 1)
    return f"{name}:{line}"


def _hover_text(hover: Any) -> str:
    """Extract the plain text from an LSP Hover (contents: str | MarkupContent | list)."""
    if not isinstance(hover, dict):
        return ""
    contents = hover.get("contents", "")
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        parts = [c.get("value", "") if isinstance(c, dict) else str(c) for c in contents]
        return "\n".join(p for p in parts if p)
    return str(contents)


def _render_lsp_result(content: str, opts: Any) -> list[str]:
    # Derive the ANSI codes from the active UI theme passed on the render
    # options — the stable styling surface for extensions — rather than importing
    # them from Tau internals (a path that is not part of the extension contract
    # and has broken across versions). muted→dim, error→red, warning→yellow.
    def _codes(fn: Any) -> tuple[str, str]:
        try:
            pre, sep, post = fn("\x00").partition("\x00")
            if sep:
                return pre, post
        except Exception:
            pass
        return "", ""

    theme = getattr(opts, "theme", None)
    DIM, RESET = _codes(getattr(theme, "muted", None) or (lambda s: s))
    RED, _ = _codes(getattr(theme, "error", None) or (lambda s: s))
    YELLOW, _ = _codes(getattr(theme, "warning", None) or (lambda s: s))

    if opts.is_error:
        lines = content.strip().splitlines() or ["(error)"]
        return [f"{RED}{line}{RESET}" for line in lines]

    metadata = opts.metadata or {}
    operation = metadata.get("operation", "")

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # Plain-text result (e.g. empty-result guidance): the LLM gets the full
        # content; the TUI shows the concise summary when one was provided.
        return [metadata.get("summary") or content.strip()]

    match operation:
        case "goToDefinition" | "goToDeclaration" | "goToTypeDefinition" | "goToImplementation":
            locs = data if isinstance(data, list) else [data]
            if not locs:
                return ["No location found"]
            summary = f"→ {_fmt_loc(locs[0])}" if len(locs) == 1 else f"{len(locs)} locations"
            out = [summary]
            out.extend(_fmt_loc(loc) for loc in locs)
            return out
        case "findReferences":
            refs = data if isinstance(data, list) else []
            word = "reference" if len(refs) == 1 else "references"
            summary = f"{len(refs)} {word}"
            out = [summary]
            out.extend(_fmt_loc(ref) for ref in refs)
            return out
        case "incomingCalls" | "outgoingCalls":
            calls = data if isinstance(data, list) else []
            label = "caller" if operation == "incomingCalls" else "callee"
            word = label if len(calls) == 1 else f"{label}s"
            summary = f"{len(calls)} {word}"
            out = [summary]
            for c in calls:
                item = (
                    c.get("from", c.get("from_", {}))
                    if operation == "incomingCalls"
                    else c.get("to", {})
                )
                name = item.get("name", "?")
                path = Path(item.get("path", "")).name if item.get("path") else "?"
                out.append(f"{name}  {DIM}{path}{RESET}")
            return out
        case "supertypes" | "subtypes":
            items = data if isinstance(data, list) else []
            label = "supertype" if operation == "supertypes" else "subtype"
            word = label if len(items) == 1 else f"{label}s"
            summary = f"{len(items)} {word}"
            out = [summary]
            for item in items:
                name = item.get("name", "?")
                path = Path(item.get("path", "")).name if item.get("path") else "?"
                out.append(f"{name}  {DIM}{path}{RESET}")
            return out
        case "hover":
            hover = data[0] if isinstance(data, list) and data else data
            text = _hover_text(hover)
            lines = [ln for ln in text.strip().splitlines() if ln.strip()]
            if not lines:
                return ["No hover info"]
            first = lines[0][:80] + ("…" if len(lines[0]) > 80 else "")
            return [first, *lines[1:]]
        case "signatureHelp":
            sigs = data.get("signatures", []) if isinstance(data, dict) else []
            active = data.get("activeSignature", 0) if isinstance(data, dict) else 0
            if not sigs:
                return ["No signature info"]
            sig = sigs[active] if active < len(sigs) else sigs[0]
            label = sig.get("label", "")
            first = label[:80] + ("…" if len(label) > 80 else "")
            out = [first]
            for i, s in enumerate(sigs):
                if i != active:
                    out.append(f"{DIM}{s.get('label', '')}{RESET}")
            return out
        case "documentSymbol":
            symbols = data if isinstance(data, list) else []
            word = "symbol" if len(symbols) == 1 else "symbols"
            summary = f"{len(symbols)} {word}"
            out: list[str] = [summary]
            def _flatten(syms: list, depth: int = 0) -> None:
                for s in syms:
                    name = s.get("name", "?")
                    detail = s.get("detail", "")
                    tail = f"  {DIM}{detail}{RESET}" if detail else ""
                    out.append(f"{'  ' * depth}{name}{tail}")
                    _flatten(s.get("children", []), depth + 1)
            _flatten(symbols)
            return out
        case "workspaceSymbol":
            symbols = data if isinstance(data, list) else []
            word = "symbol" if len(symbols) == 1 else "symbols"
            summary = f"{len(symbols)} {word}"
            out = [summary]
            for s in symbols:
                name = s.get("name", "?")
                loc = s.get("location", {})
                path = Path(loc.get("path", "")).name if loc.get("path") else "?"
                line = loc.get("range", {}).get("start", {}).get("line", 1)
                out.append(f"{name}  {DIM}{path}:{line}{RESET}")
            return out
        case "rename":
            if not isinstance(data, dict):
                return ["No changes"]
            if not data.get("applied"):
                return [data.get("reason", "No changes")]
            return [data.get("summary", "Applied")]
        case "codeAction":
            if not isinstance(data, dict):
                return ["No actions"]
            if data.get("applied"):
                return [data.get("summary", "Applied")]
            actions = data.get("actions", [])
            if not actions:
                return ["No actions available"]
            word = "action" if len(actions) == 1 else "actions"
            summary = f"{len(actions)} {word}"
            out = [summary]
            for a in actions:
                title = a.get("title", "?")
                kind = a.get("kind", "")
                tail = f"  {DIM}{kind}{RESET}" if kind else ""
                out.append(f"{title}{tail}")
            return out
        case "formatting" | "rangeFormatting":
            if not isinstance(data, dict):
                return ["No edits"]
            if not data.get("applied"):
                return [data.get("reason", "Already formatted")]
            return [data.get("summary", "Applied")]
        case "inlayHint":
            hints = data if isinstance(data, list) else []
            word = "hint" if len(hints) == 1 else "hints"
            summary = f"{len(hints)} {word}"
            out = [summary]
            for h in hints:
                pos = h.get("position", {})
                ln = pos.get("line", 1)
                ch = pos.get("character", 1)
                label = h.get("label", "")
            if isinstance(label, list):
                # Join label parts safely, split across lines for readability
                label = "".join(
                    p.get("value", "") if isinstance(p, dict) else str(p)
                    for p in label
                )
            kind = h.get("kind", 0)
            kind_tag = "type" if kind == 1 else "param" if kind == 2 else ""
            tag = f"  {DIM}[{kind_tag}]{RESET}" if kind_tag else ""
            out.append(f"{ln}:{ch}  {label}{tag}")
            return out
        case "codeLens":
            lenses = data if isinstance(data, list) else []
            word = "lens" if len(lenses) == 1 else "lenses"
            summary = f"{len(lenses)} {word}"
            out = [summary]
            for lens in lenses:
                start = lens.get("range", {}).get("start", {})
                ln = start.get("line", 1)
                command = lens.get("command", {})
                title = command.get("title", "?") if isinstance(command, dict) else "?"
                snippet = lens.get("snippet", "")
                ctx = f"  {DIM}{snippet[:50]}{RESET}" if snippet else ""
                out.append(f"{ln}:  {title}{ctx}")
            return out
        case "diagnostics":
            diags = data if isinstance(data, list) else []
            if not diags:
                return ["No diagnostics"]
            errors = sum(1 for d in diags if d.get("severity") == 1)
            warnings = sum(1 for d in diags if d.get("severity") == 2)
            parts: list[str] = []
            if errors:
                parts.append(f"{RED}{errors} {'error' if errors == 1 else 'errors'}{RESET}")
            if warnings:
                parts.append(
                    f"{YELLOW}{warnings} {'warning' if warnings == 1 else 'warnings'}{RESET}"
                )
            if not parts:
                parts = [f"{len(diags)} diagnostics"]
            summary = ", ".join(parts)
            out = [summary]
            for d in diags:
                sev = d.get("severity", 1)
                start = d.get("range", {}).get("start", {})
                ln = start.get("line", 1)
                ch = start.get("character", 1)
                msg = d.get("message", "")
                src = d.get("source", "")
                color = RED if sev == 1 else YELLOW if sev == 2 else DIM
                tag = f"  {DIM}[{src}]{RESET}" if src else ""
                out.append(f"{color}{ln}:{ch}{RESET}  {msg}{tag}")
            return out
        case "completion":
            items = (
                data
                if isinstance(data, list)
                else (data.get("items", []) if isinstance(data, dict) else [])
            )
            word = "completion" if len(items) == 1 else "completions"
            summary = f"{len(items)} {word}"
            out = [summary]
            for item in items[:20]:
                label = item.get("label", "?")
                detail = item.get("detail", "")
                tail = f"  {DIM}{detail}{RESET}" if detail else ""
                out.append(f"{label}{tail}")
            if len(items) > 20:
                out.append(f"{DIM}··· {len(items) - 20} more{RESET}")
            return out
        case "documentHighlight":
            highlights = data if isinstance(data, list) else []
            word = "highlight" if len(highlights) == 1 else "highlights"
            summary = f"{len(highlights)} {word}"
            out = [summary]
            for h in highlights:
                start = h.get("range", {}).get("start", {})
                ln = start.get("line", 1)
                kind = h.get("kind", 1)
                kind_tag = "read" if kind == 2 else "write" if kind == 3 else "text"
                snippet = h.get("snippet", "")
                ctx = f"  {DIM}{snippet.strip()[:50]}{RESET}" if snippet else ""
                out.append(f"{ln}  [{kind_tag}]{ctx}")
            return out
        case "selectionRange":
            ranges = data if isinstance(data, list) else []
            summary = f"{len(ranges)} selection range(s)"
            out = [summary]
            def _show_selection(r: dict, depth: int = 0) -> None:
                if not r:
                    return
                s = r.get("range", {}).get("start", {})
                e = r.get("range", {}).get("end", {})
                sl, el = s.get("line", 1), e.get("line", 1)
                out.append(f"{'  ' * depth}{sl}–{el}")
                _show_selection(r.get("parent") or {}, depth + 1)
            for r in ranges:
                _show_selection(r)
            return out
        case "foldingRange":
            ranges = data if isinstance(data, list) else []
            word = "range" if len(ranges) == 1 else "ranges"
            summary = f"{len(ranges)} folding {word}"
            out = [summary]
            for r in ranges:
                sl = r.get("startLine", 0) + 1
                el = r.get("endLine", 0) + 1
                kind = r.get("kind", "")
                tail = f"  {DIM}[{kind}]{RESET}" if kind else ""
                out.append(f"{sl}–{el}{tail}")
            return out
        case "documentLink":
            links = data if isinstance(data, list) else []
            word = "link" if len(links) == 1 else "links"
            summary = f"{len(links)} document {word}"
            out = [summary]
            for link in links:
                start = link.get("range", {}).get("start", {})
                ln = start.get("line", 1)
                target = link.get("target", "")
                short = (target[:60] + "…") if len(target) > 60 else target
                out.append(f"{ln}  {DIM}{short}{RESET}")
            return out
        case "semanticTokens":
            if not isinstance(data, dict):
                return ["No semantic tokens"]
            token_count = len(data.get("data", [])) // 5
            result_id = data.get("resultId", "")
            rid_tag = f"  {DIM}resultId={result_id}{RESET}" if result_id else ""
            return [f"{token_count} semantic token(s){rid_tag}"]
        case "inlineValue":
            values = data if isinstance(data, list) else []
            word = "value" if len(values) == 1 else "values"
            summary = f"{len(values)} inline {word}"
            out = [summary]
            for v in values:
                start = v.get("range", {}).get("start", {})
                ln = start.get("line", 1)
                text = v.get("text") or v.get("expression") or v.get("variableName") or "?"
                out.append(f"{ln}  {text}")
            return out
        case "moniker":
            monikers = data if isinstance(data, list) else []
            if not monikers:
                return ["No moniker"]
            summary = f"{len(monikers)} moniker(s)"
            out = [summary]
            for m in monikers:
                identifier = m.get("identifier", "?")
                scheme = m.get("scheme", "")
                kind = m.get("kind", "")
                tags = "  ".join(filter(None, [scheme, kind]))
                tail = f"  {DIM}[{tags}]{RESET}" if tags else ""
                out.append(f"{identifier}{tail}")
            return out
        case "linkedEditingRange":
            if not isinstance(data, dict):
                return ["No linked ranges"]
            ranges = data.get("ranges", [])
            word = "range" if len(ranges) == 1 else "ranges"
            summary = f"{len(ranges)} linked {word}"
            out = [summary]
            for r in ranges:
                s = r.get("start", {})
                e = r.get("end", {})
                out.append(
                    f"{s.get('line', 1)}:{s.get('character', 1)} \u2013 "
                    f"{e.get('line', 1)}:{e.get('character', 1)}"
                )
            word_pattern = data.get("wordPattern", "")
            if word_pattern:
                out.append(f"{DIM}pattern: {word_pattern}{RESET}")
            return out
        case _:
            lines = content.strip().splitlines()
            return lines if lines else ["Done"]


class LSPParams(BaseModel):
    operation: Operation = Field(
        description="LSP operation to perform.",
        examples=["goToDefinition", "findReferences", "hover", "rename", "diagnostics"],
    )
    file_path: str = Field(
        default="",
        description=(
            "Absolute path to the file to inspect. "
            "Required for all ops except workspaceSymbol (a project-wide search)."
        ),
        examples=["/home/user/project/src/main.py", "/home/user/project/src/utils.ts"],
    )
    line: int = Field(
        default=1,
        ge=1,
        description="1-based line number (required for position-based ops).",
        examples=[42, 100],
    )
    character: int = Field(
        default=1,
        ge=1,
        description="1-based character offset (required for position-based ops).",
        examples=[15, 8],
    )
    end_line: int = Field(
        default=1,
        ge=1,
        description="1-based end line (required for codeAction).",
        examples=[42, 105],
    )
    end_character: int = Field(
        default=1,
        ge=1,
        description="1-based end character (required for codeAction).",
        examples=[30, 20],
    )
    new_name: str = Field(
        default="",
        description="New symbol name (required for rename).",
        examples=["parse_config", "UserService", "handleRequest"],
    )
    query: str = Field(
        default="",
        description="Search query (optional for workspaceSymbol).",
        examples=["ClassName", "parseConfig", "handle_process"],
    )
    action_kind: str = Field(
        default="",
        description=(
            "codeAction only: when set, auto-applies the first action "
            "matching this kind and writes "
            "changes to disk. Common values: 'quickfix', 'refactor', 'source.fixAll', "
            "'source.organizeImports'. Omit to list available actions without applying."
        ),
        examples=["quickfix", "source.organizeImports", "refactor"],
    )


_DESCRIPTION = (
    "Query language servers (pyright, ruff, gopls, rust-analyzer, …) for code intelligence.\n\n"
    "Navigation:      goToDefinition, goToImplementation\n"
    "References:      findReferences, incomingCalls, outgoingCalls\n"
    "Symbol info:     hover, documentSymbol, workspaceSymbol\n"
    "Diagnostics:     diagnostics\n"
    "Edits:           rename (requires new_name), codeAction, formatting\n\n"
    "line and character are 1-based. "
    "codeAction also requires end_line and end_character. "
    "rename requires new_name. "
    "codeAction accepts action_kind to auto-apply the first matching action."
)


def _empty_result_message(params: LSPParams) -> str:
    """Operation-aware guidance for an empty result, so the model can self-correct.

    Empty often means the request was well-formed but pointed at the wrong place
    (bad position, wrong query) rather than a genuine absence — say which.
    """
    op = params.operation
    base = f"No results for {op}."

    if op == "diagnostics":
        # Empty diagnostics is success, not a failure.
        return "No diagnostics reported — the file has no errors or warnings."
    if op == "documentSymbol":
        return (
            base + " The file may be empty, have no top-level definitions, "
            "contain syntax errors, or not be indexed yet by the language server."
        )
    if op == "workspaceSymbol":
        q = params.query or "(empty)"
        return (
            base + f" No symbol matches query {q!r}. workspaceSymbol does prefix/substring "
            "matching — try a shorter or differently-cased name; an empty query lists all symbols."
        )
    if op in _POSITION_OPS:
        msg = (
            base + f" The position {params.line}:{params.character} may not be on the symbol "
            "name (line/character are 1-based and must point at the identifier). "
            "Locate it first with documentSymbol or workspaceSymbol, then retry at that position."
        )
        if op in _CALL_HIERARCHY_OPS:
            msg += (
                " For call hierarchy, anchor on a call site or a symbol reference "
                "rather than its declaration."
            )
        if op == "goToImplementation":
            msg += (
                " goToImplementation resolves concrete implementations of an interface, trait, "
                "abstract method, or protocol; it returns nothing for an already-concrete symbol "
                "— use goToDefinition for those."
            )
        return msg
    return base


class LSPTool(Tool):
    def __init__(self, service: LSP) -> None:
        super().__init__(
            name="lsp",
            description=_DESCRIPTION,
            schema=LSPParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_lsp_result,
            render_call=_render_lsp_call,
            render_shell="default",
            prompt_guidelines=(
                "Use for precise code intelligence: goToDefinition, findReferences, "
                "incomingCalls/outgoingCalls, hover, rename, diagnostics. Prefer over grep "
                "when the language server is available.\n"
                "Before editing an unfamiliar symbol, goToDefinition (and hover) to understand it; "
                "before renaming or changing a signature, findReferences to gauge blast radius. "
                "Diagnostics are surfaced automatically after every edit, so read them rather than "
                "assuming a change is correct.\n"
                "Position-based ops (goToDefinition, goToImplementation, findReferences, "
                "incomingCalls, outgoingCalls, hover, rename) need line/character pointing at "
                "the symbol name itself (1-based). If you don't know the position, first locate "
                "the symbol with documentSymbol (within a file) or workspaceSymbol (by name), "
                "then call the op at that symbol's line/character. "
                "For call hierarchy, anchor on a call site or a symbol reference rather than "
                "its declaration."
            ),
        )
        self._service = service

    def get_display_name(self, args: dict[str, Any]) -> str:
        op = args.get("operation", "lsp")
        path = args.get("file_path", "")
        line = args.get("line", "")
        name = Path(path).name if path else ""
        return f"{op} {name}:{line}" if name else op

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = LSPParams.model_validate(invocation.params)

        if params.operation in NEEDS_NAME and not params.new_name:
            return ToolResult.error(invocation.id, f"'{params.operation}' requires new_name.")
        if params.operation in NEEDS_RANGE and (
            params.end_line < params.line
            or (params.end_line == params.line and params.end_character < params.character)
        ):
            return ToolResult.error(
                invocation.id, "end_line/end_character must be >= line/character."
            )

        # Normalize to an absolute path anchored at the runtime cwd. The model
        # often passes project-relative paths; get_clients (root resolution,
        # boundary check) and diagnostics (keyed by absolute path) only work on
        # absolute paths, so resolve here once for all downstream callers.
        def _resolve(p: str) -> str:
            raw = Path(p)
            return str((raw if raw.is_absolute() else Path(self._service._cwd) / raw).resolve())

        needs_index = params.operation in _INDEX_OPS

        if params.operation == "workspaceSymbol":
            # Project-wide search — needs no specific file; it queries whatever
            # servers are already running. A file_path is optional: if given, we
            # touch it to make sure at least one server is warmed first.
            file = _resolve(params.file_path) if params.file_path else ""
            if file and Path(file).exists():
                await self._service.touch_file(file, wait_for_diagnostics=True)
        else:
            if not params.file_path:
                return ToolResult.error(invocation.id, f"'{params.operation}' requires file_path.")
            file = _resolve(params.file_path)
            if not Path(file).exists():
                return ToolResult.error(invocation.id, f"File not found: {params.file_path}")
            if not await self._service.has_clients(file):
                return ToolResult.error(
                    invocation.id, "No LSP server available for this file type."
                )
            await self._service.touch_file(
                file, wait_for_diagnostics=(params.operation == "diagnostics" or needs_index)
            )

        # Convert to 0-based (LSP protocol)
        line = params.line - 1
        char = params.character - 1
        end_line = params.end_line - 1
        end_char = params.end_character - 1

        try:
            result = await self._run(params, file, line, char, end_line, end_char)
        except Exception as exc:
            return ToolResult.error(invocation.id, str(exc))

        metadata = {"operation": params.operation}

        if not result:
            # Full guidance to the LLM (so it can self-correct); concise first
            # sentence to the TUI via metadata["summary"] (mirrors how successful
            # results render a short summary while the LLM gets the full payload).
            msg = _empty_result_message(params)
            metadata["summary"] = msg.split('. ', 1)[0].rstrip('.') + '.'
            return ToolResult.ok(invocation.id, msg, metadata=metadata)

        # Post-process raw LSP output: convert file:// URIs to relative paths,
        # shift line/character to 1-based, and attach code snippets.
        if (
            params.operation not in _EDIT_OPS
            and params.operation not in _RAW_OPS
            and isinstance(result, (list, dict))
        ):
            cwd = self._service._cwd
            result = _normalize(result, cwd)
            if params.operation in _NAV_OPS:
                result = _add_snippets(result, cwd, max_lines=10)
            elif params.operation in _SNIPPET_OPS:
                result = _add_snippets(result, cwd, max_lines=1)

        return ToolResult.ok(invocation.id, json.dumps(result, indent=2), metadata=metadata)

    async def _run(
        self,
        params: LSPParams,
        file: str,
        line: int,
        char: int,
        end_line: int,
        end_char: int,
    ) -> Any:
        svc = self._service
        match params.operation:
            # ── Navigation ───────────────────────────────────────────────────
            case "goToDefinition":
                return await svc.definition(file, line, char)
            case "goToImplementation":
                return await svc.implementation(file, line, char)
            # ── References & calls ───────────────────────────────────────────
            case "findReferences":
                return await svc.references(file, line, char)
            case "incomingCalls":
                return await svc.incoming_calls(file, line, char)
            case "outgoingCalls":
                return await svc.outgoing_calls(file, line, char)
            # ── Symbol info ──────────────────────────────────────────────────
            case "hover":
                # Drop hovers with no usable text (servers sometimes return an
                # empty-content hover at keyword/whitespace positions, or before
                # analysis finishes). Collapsing to [] lets execute() emit the
                # positioning guidance instead of a useless empty hover object.
                hovers = await svc.hover(file, line, char)
                return [h for h in hovers if _hover_text(h).strip()]
            case "documentSymbol":
                return await svc.document_symbol(file)
            case "workspaceSymbol":
                return await svc.workspace_symbol(params.query)
            # ── Edits (auto-applied to disk) ─────────────────────────────────
            case "rename":
                applied = await svc.rename(file, line, char, params.new_name)
                if not applied:
                    return {"applied": False, "reason": "No changes returned by language server."}
                total = sum(applied.values())
                files = list(applied.keys())
                return {
                    "applied": True,
                    "new_name": params.new_name,
                    "files": files,
                    "total_edits": total,
                    "summary": (
                        f"Renamed to '{params.new_name}' in {len(files)} file(s) "
                        f"({total} edit(s)): {', '.join(files)}"
                    ),
                }
            case "codeAction":
                actions = await svc.code_action(file, line, char, end_line, end_char)
                if not actions:
                    return {"actions": [], "applied": False}
                if not params.action_kind:
                    # No kind specified — return list for inspection
                    return {"actions": actions, "applied": False}
                # Find first action whose kind starts with the requested kind
                match = next(
                    (a for a in actions if a.get("kind", "").startswith(params.action_kind)),
                    None,
                )
                if match is None:
                    available = [a.get("kind", "") for a in actions]
                    return {
                        "applied": False,
                        "reason": f"No action with kind '{params.action_kind}' found.",
                        "available_kinds": available,
                    }
                applied = await svc.apply_code_action(match)
                if applied is None:
                    return {
                        "applied": False,
                        "action": match.get("title", ""),
                        "reason": "Action carries neither an edit nor a command.",
                    }
                total = sum(applied.values())
                files = list(applied.keys())
                if files:
                    summary = (
                        f"Applied '{match.get('title', '')}' in {len(files)} file(s) "
                        f"({total} edit(s)): {', '.join(files)}"
                    )
                else:
                    summary = (
                        f"Executed '{match.get('title', '')}' command (edits applied server-side)."
                    )
                return {
                    "applied": True,
                    "action": match.get("title", ""),
                    "kind": match.get("kind", ""),
                    "files": files,
                    "total_edits": total,
                    "summary": summary,
                }
            case "formatting":
                applied = await svc.formatting(file)
                if not applied:
                    return {
                        "applied": False,
                        "reason": "No formatting edits returned (file may already be formatted).",
                    }
                total = sum(applied.values())
                return {
                    "applied": True,
                    "files": list(applied.keys()),
                    "total_edits": total,
                    "summary": f"Formatted {list(applied.keys())[0]} ({total} edit(s) applied).",
                }
            # ── Diagnostics ──────────────────────────────────────────────────
            case "diagnostics":
                all_diags = await svc.diagnostics()
                return all_diags.get(file, [])
