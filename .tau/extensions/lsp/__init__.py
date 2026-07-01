"""
LSP extension for Tau — gives the AI agent code intelligence via language servers.

Follows opencode's approach:
  - spawns language servers as subprocesses (pyright, ruff, gopls, rust-analyzer, …)
  - communicates via JSON-RPC 2.0 over stdin/stdout (Content-Length framing)
  - lazily spawns one client per (server_id, project_root) pair
  - exposes a single `lsp` tool with 18 operations

Deep integration (beyond the tool level, mirrors opencode):
  - read tool   → silently opens file in LSP background so indexing starts immediately
  - write tool  → waits for fresh diagnostics, appends errors to tool result
  - edit tool   → same as write
  - context     → injects current error diagnostics before each LLM turn

Install location: .tau/extensions/lsp/  (project-local)
              or: ~/.tau/extensions/lsp/ (global)

Optional settings.json config:
  {
    "extensions": {
      "list": [{
        "path": ".tau/extensions/lsp",
        "settings": {
          "lsp": true,
          "servers": {
            "pyright": { "enabled": true },
            "ruff":    { "enabled": false },
            "my-ls":   { "command": ["my-ls", "--stdio"], "extensions": [".xyz"] }
          }
        }
      }]
    }
  }
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from tau.hooks.engine import ContextEventResult, ToolResultEventResult
from tau.hooks.runtime import InputEventResult

from .core import LSP
from .tool import LSPTool

_WRITE_TOOLS = {"write", "edit"}
_MAX_ERRORS_PER_FILE = 20
_MAX_OTHER_FILES = 3

# Matches @path/to/file.ext:line  or  @path/to/file.ext:start-end
# Requires a dot in the filename so we don't accidentally match @user:pass etc.
_AT_LINE_RE = re.compile(r"@([^\s@:]+\.[^\s@:/]+(?:[^\s@:]*)):(\d+)(?:-(\d+))?")


async def _expand_file_line_refs(text: str, service: LSP, cwd: Path) -> str | None:
    """
    Find @file:line (and @file:start-end) mentions in *text* that were skipped
    by the TUI's _expand_at_mentions (because 'file:line' isn't a valid path).

    For single-line pins (@file:N) we ask LSP for the symbol whose range starts
    on that line and expand the window to cover the full symbol body.
    For explicit ranges (@file:N-M) we skip LSP and just read those lines directly.

    Each @ref is replaced IN-PLACE with a <file> block so the AI sees the
    content directly and doesn't feel the need to call the read tool.
    Returns the modified string, or None if nothing was expanded.
    """
    matches = list(_AT_LINE_RE.finditer(text))
    if not matches:
        return None

    # Map: match.start() → replacement string (or None = leave as-is)
    replacements: dict[int, tuple[int, str]] = {}  # start → (end, replacement)
    seen: set[tuple[str, int, int]] = set()

    for m in matches:
        raw_path, start_str, end_str = m.group(1), m.group(2), m.group(3)
        file = Path(raw_path) if Path(raw_path).is_absolute() else cwd / raw_path
        if not file.is_file():
            continue

        start_line = int(start_str)  # 1-based
        end_line = int(end_str) if end_str else None

        # ── Explicit range: just read directly ──────────────────────────────
        if end_line is not None:
            key = (str(file), start_line, end_line)
            if key in seen:
                replacements[m.start()] = (m.end(), "")
                continue
            seen.add(key)
            file_lines = file.read_text(errors="replace").splitlines()
            snippet = "\n".join(file_lines[start_line - 1 : end_line])
            block = f'<file path="{raw_path}" lines="{start_line}-{end_line}">\n{snippet}\n</file>'
            replacements[m.start()] = (m.end(), block)
            continue

        # ── Single-line pin: use LSP to find the enclosing symbol ───────────
        key = (str(file), start_line, start_line)
        if key in seen:
            replacements[m.start()] = (m.end(), "")
            continue
        seen.add(key)

        _FALLBACK_WINDOW = 25  # lines above+below when LSP has no symbols yet
        sym_start = max(1, start_line - _FALLBACK_WINDOW)
        sym_end = start_line + _FALLBACK_WINDOW

        try:
            await service.touch_file(str(file), wait_for_diagnostics=False)
            symbols = await service.document_symbol(str(file))
            # Cold-start: server just spawned and hasn't indexed the file yet.
            # Wait briefly and retry once before falling back to the line window.
            if not symbols:
                await asyncio.sleep(1.5)
                symbols = await service.document_symbol(str(file))
            target_line = start_line - 1  # 0-based for LSP
            best_span = None  # (s_line, e_line) in 0-based LSP coords

            def _walk(syms: list, _tgt: int = target_line) -> None:
                nonlocal best_span
                for sym in syms:
                    r = sym.get("range") or sym.get("location", {}).get("range")
                    if not r:
                        continue
                    s = r["start"]["line"]
                    e = r["end"]["line"]
                    if s <= _tgt <= e and (
                        best_span is None
                        or s > best_span[0]
                        or (s == best_span[0] and (e - s) > (best_span[1] - best_span[0]))
                    ):
                        # Prefer the symbol that starts on or closest before the
                        # target line, breaking ties by largest span (full body).
                        best_span = (s, e)
                    _walk(sym.get("children") or [], _tgt)

            _walk(symbols)
            # Only use LSP span if it covers more than one line; a 1-line span
            # usually means we only got the declaration, not the body.
            if best_span is not None and (best_span[1] - best_span[0]) >= 1:
                sym_start = best_span[0] + 1  # back to 1-based
                sym_end = best_span[1] + 1
        except Exception:
            pass  # fall back to ±25-line window around the pinned line

        file_lines = file.read_text(errors="replace").splitlines()
        sym_end = min(sym_end, len(file_lines))
        snippet = "\n".join(file_lines[sym_start - 1 : sym_end])
        label = f"{sym_start}-{sym_end}" if sym_end != sym_start else str(sym_start)
        block = f'<file path="{raw_path}" lines="{label}">\n{snippet}\n</file>'
        replacements[m.start()] = (m.end(), block)

    if not replacements:
        return None

    # Rebuild text by replacing each matched span with its block in reverse order
    # (reverse so positions stay valid as we mutate the string).
    result = text
    for start in sorted(replacements, reverse=True):
        end, block = replacements[start]
        result = result[:start] + block + result[end:]

    return result


_SEVERITY = {1: "ERROR", 2: "WARN", 3: "INFO", 4: "HINT"}
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _build_diag_block(diagnostics: list[dict]) -> dict:
    """Build an _extra_blocks entry containing complete diagnostic output."""
    errors = sum(1 for d in diagnostics if d.get("severity") == 1)
    warnings = sum(1 for d in diagnostics if d.get("severity") == 2)
    parts = []
    if errors:
        parts.append(f"{_RED}✘ {errors} {'error' if errors == 1 else 'errors'}{_RESET}")
    if warnings:
        parts.append(f"{_YELLOW}★ {warnings} {'warning' if warnings == 1 else 'warnings'}{_RESET}")
    summary = "  ".join(parts) if parts else "diagnostics"

    detail: list[str] = []
    for d in diagnostics[:_MAX_ERRORS_PER_FILE]:
        start = d.get("range", {}).get("start", {})
        line = start.get("line", 0) + 1
        sev = d.get("severity", 1)
        msg = d.get("message", "").replace("\n", " ")
        src = d.get("source", "")
        icon = "✘" if sev == 1 else "★" if sev == 2 else "·"
        col = _RED if sev == 1 else _YELLOW if sev == 2 else _DIM
        src_tag = f"  {_DIM}[{src}]{_RESET}" if src else ""
        detail.append(f"{col}{icon}  {line}  {msg}{src_tag}{_RESET}")
    if len(diagnostics) > _MAX_ERRORS_PER_FILE:
        detail.append(f"{_DIM}··· {len(diagnostics) - _MAX_ERRORS_PER_FILE} more{_RESET}")

    return {"lines": [summary, *detail], "preview_lines": 1}


def _diag_lines(diagnostics: list[dict]) -> list[str]:
    lines: list[str] = []
    for d in diagnostics[:_MAX_ERRORS_PER_FILE]:
        start = d.get("range", {}).get("start", {})
        line = start.get("line", 0) + 1
        col = start.get("character", 0) + 1
        sev = _SEVERITY.get(d.get("severity", 1), "ERROR")
        msg = d.get("message", "").replace("\n", " ")
        src = d.get("source", "")
        source = f"[{src}] " if src else ""
        lines.append(f"{sev} {source}[{line}:{col}] {msg}")
    if len(diagnostics) > _MAX_ERRORS_PER_FILE:
        lines.append(f"... and {len(diagnostics) - _MAX_ERRORS_PER_FILE} more")
    return lines


def _format_diagnostics_plain(diagnostics: list[dict]) -> str:
    """Plain text for TUI display under tool results."""
    return "\n".join(_diag_lines(diagnostics))


def _format_diagnostics_xml(file: str, diagnostics: list[dict]) -> str:
    """XML-wrapped for LLM context injection."""
    inner = "\n".join(f"  {line}" for line in _diag_lines(diagnostics))
    return f'<diagnostics file="{file}">\n{inner}\n</diagnostics>'


def register(tau) -> None:
    service = LSP(cwd=tau.cwd)
    service.apply_config(tau.config)
    lsp_enabled: bool = tau.config.get("lsp", True)

    tau.register_tool(LSPTool(service))

    # ── eager server warm-up ──────────────────────────────────────────────────
    # eager not set / []     → auto-detect: walk project via pygments + gitignore,
    #                          start only servers whose languages are present
    # eager: ["pyright"]     → explicit: start exactly the listed servers
    #
    # Triggered on `runtime_ready` rather than inline here, so warm-up starts from
    # a defined point where the whole runtime is wired (engine, agent, tools,
    # extensions) instead of mid-construction during register().

    async def _warmup_now() -> None:
        if not lsp_enabled:
            return
        eager_ids: list[str] | None = (
            tau.config.get("eager") or None
        )  # absent/[] → None = auto-detect
        await service.eager(eager_ids)

    @tau.on("runtime_ready")
    async def _warmup(event, ctx):
        asyncio.ensure_future(_warmup_now())

    # ── 0. input → expand @file:line / @file:start-end mentions ─────────────

    @tau.on("input")
    async def _on_input(event, ctx):
        if not lsp_enabled:
            return None
        new_text = await _expand_file_line_refs(event.text, service, Path(tau.cwd))
        if new_text is None:
            return None
        return InputEventResult(action="transform", text=new_text)

    # ── 1. read/write/edit → start LSP on-demand and surface diagnostics ────────

    @tau.on("tool_result")
    async def _on_read(event, ctx):
        if not lsp_enabled or event.tool_name != "read" or event.is_error:
            return None
        file: str | None = event.input.get("path")
        if not file or not Path(file).exists():
            return None

        # Don't block the read on the language server — opening a file the model
        # is merely reading should never stall the tool result for up to 5s while
        # the server is still indexing. Touch the file fire-and-forget so indexing
        # starts, and only surface diagnostics that are ALREADY available. Anything
        # the server publishes later still reaches the model via the `context` hook
        # below, which injects active errors before each turn.
        await service.touch_file(file, wait_for_diagnostics=False)
        all_diags = await service.diagnostics()
        norm = str(Path(file).resolve())
        issues = [d for d in all_diags.get(norm, []) if d.get("severity") in (1, 2, 3)]
        if not issues:
            return None
        return ToolResultEventResult(
            content=event.content + "\n\n" + _format_diagnostics_plain(issues),
            metadata={
                "_display_content": event.content,
                "_extra_blocks": [_build_diag_block(issues)],
            },
        )

    # ── 2. write/edit tools → report LSP errors to agent and user ────────────

    @tau.on("tool_result")
    async def _on_write(event, ctx):
        if not lsp_enabled or event.tool_name not in _WRITE_TOOLS or event.is_error:
            return None
        file: str | None = event.input.get("path")
        if not file or not Path(file).exists():
            return None

        await service.touch_file(file, wait_for_diagnostics=True)
        all_diags = await service.diagnostics()
        norm = str(Path(file).resolve())

        current_issues = [d for d in all_diags.get(norm, []) if d.get("severity") in (1, 2, 3)]

        other_issues: list[dict] = []
        other_count = 0
        for other_path, diags in all_diags.items():
            if other_path == norm or other_count >= _MAX_OTHER_FILES:
                continue
            errors = [d for d in diags if d.get("severity") == 1]
            if errors:
                other_issues.extend(errors)
                other_count += 1

        all_issues = current_issues + other_issues
        if not all_issues:
            return None

        return ToolResultEventResult(
            content=event.content + "\n\n" + _format_diagnostics_plain(all_issues),
            metadata={
                "_display_content": event.content,
                "_extra_blocks": [_build_diag_block(all_issues)],
            },
        )

    # ── 3. context → inject active diagnostics before each LLM turn ──────────

    @tau.on("context")
    async def _on_context(event, _ctx):
        if not lsp_enabled:
            return None
        all_diags = await service.diagnostics()

        blocks: list[str] = []
        for path, diags in all_diags.items():
            errors = [d for d in diags if d.get("severity") == 1]
            if errors:
                blocks.append(_format_diagnostics_xml(path, errors))

        if not blocks:
            return None

        injection = (
            "<lsp_diagnostics>\n"
            "The following errors are currently reported by the language server:\n"
            + "\n".join(blocks)
            + "\n</lsp_diagnostics>"
        )

        # Prepend as a system message at the start of the context
        messages = list(event.messages)
        messages.insert(0, {"role": "system", "content": injection})
        return ContextEventResult(messages=messages)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @tau.on("runtime_stop")
    async def _shutdown(_event, _ctx):
        # Tear down spawned language servers on quit. Uses `runtime_stop` rather
        # than `session_shutdown` (which only fires on session transitions, not on
        # actual exit) so the servers are reaped instead of leaking to the OS.
        await service.shutdown()

    @tau.on("extension_unload")
    async def _on_unload(_event, _ctx):
        # Fired right before this extension is replaced by a settings reload.
        # Reap the running servers so the incoming instance starts clean instead
        # of leaking the old subprocesses.
        await service.shutdown()

    @tau.on("extension_reloaded")
    async def _on_reloaded(_event, _ctx):
        # The replacement instance is now wired with fresh config — re-run warm-up
        # (runtime_ready only fires once at startup, not on reload).
        asyncio.ensure_future(_warmup_now())
