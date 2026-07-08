"""In-process subagent execution.

Runs one agent turn directly in this interpreter via Engine/LLM, instead of
spawning a separate `tau` OS process (the approach subagent_tool.py uses).

Deliberately narrow and self-contained: builds an Engine, an LLM, fresh tool
instances, and a Hooks() dispatcher directly — bypassing Runtime.create()'s
full bootstrap (resource discovery, extension loading, global registry
reload) entirely. That bootstrap mutates process-wide singletons
(skill_registry, prompt_registry, theme_registry) and re-fires session_start
on every extension, which is unsafe to run reentrantly from inside an
already-running session (see the /workflow README for the full writeup).
Engine itself is explicitly documented as knowing "nothing about sessions,
extensions, or compaction" — exactly the isolation this needs.

Trade-off: an embedded agent only has the base builtin coding tools
(read/write/edit/terminal/glob/grep/ls) plus whatever its own `tools:`
frontmatter names from that set — extension-contributed tools (web_search,
web_fetch, subagent, todo, ...) are not available, since loading them would
mean loading extensions. None of the shipped subagent presets need more than
the base set.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tau.builtins.tools import TOOLS
from tau.engine import Engine, EngineContext
from tau.hooks.engine import MessageEndEvent, ToolExecutionStartEvent
from tau.hooks.service import Hooks
from tau.inference import StopReason
from tau.inference.api.text.service import TextLLM
from tau.message.types import Role, UserMessage
from tau.tool.types import Tool

TASK_TIMEOUT_S = 300
_ABORT_GRACE_S = 15
_TOOL_ARG_KEYS = ("cmd", "pattern", "path")

# Same fallback Runtime.create() uses when no model is configured — matched
# here so an embedded agent behaves the same as a real session would.
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_PROVIDER = "anthropic"


def _tool_preview(name: str, args: dict[str, Any]) -> str:
    """One-line summary of a tool call, e.g. 'terminal: curl -s https://...'."""
    for key in _TOOL_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return f"{name}: {value}"[:100]
    for value in args.values():
        if isinstance(value, str) and value:
            return f"{name}: {value}"[:100]
    return name


def _build_tools(tool_names: list[str] | None) -> list[Tool]:
    """Fresh instances of the requested tools (or the full base set if unset)."""
    selected = TOOLS if not tool_names else [t for t in TOOLS if t.name in set(tool_names)]
    return [t.__class__() for t in selected]  # type: ignore[call-arg]


async def run_embedded_agent(
    *,
    cwd: Path,
    model_id: str | None,
    provider: str | None,
    system_prompt: str,
    tool_names: list[str] | None,
    task_text: str,
    on_tool_start: Callable[[str], None] | None = None,
    timeout_s: float = TASK_TIMEOUT_S,
) -> tuple[bool, str, dict[str, Any]]:
    """Run one subagent turn to completion, bounded by ``timeout_s``.

    Returns (ok, output_text, usage). Fully self-contained: its own LLM
    instance, its own Hooks(), its own fresh tools, its own message history —
    no session persistence, no shared registries, no OS subprocess.

    On timeout, aborts cooperatively via the engine's own AbortSignal (the
    same mechanism Esc-cancel uses in the interactive TUI) and gives it
    ``_ABORT_GRACE_S`` to unwind cleanly before falling back to raw task
    cancellation.
    """
    usage: dict[str, Any] = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    final_text = ""
    error_message: str | None = None
    failed = False

    try:
        llm = TextLLM(model_id=model_id or _DEFAULT_MODEL, provider=provider or _DEFAULT_PROVIDER)
    except Exception as e:
        return False, f"Failed to resolve model: {e}", usage

    tools = _build_tools(tool_names)
    hooks = Hooks()

    def _on_message_end(event: MessageEndEvent) -> None:
        nonlocal final_text, error_message, failed
        message = event.message
        if getattr(message, "role", None) != Role.ASSISTANT:
            return
        usage["turns"] += 1
        usage["input_tokens"] += message.usage.input_tokens
        usage["output_tokens"] += message.usage.output_tokens
        usage["cost"] += message.usage.cost.total
        if message.stop_reason in (StopReason.Error, StopReason.Abort):
            failed = True
        if message.error:
            error_message = message.error
            failed = True
        text = message.text_content()
        if text:
            final_text = text

    def _on_tool_start(event: ToolExecutionStartEvent) -> None:
        if on_tool_start is not None:
            on_tool_start(_tool_preview(event.tool_call.name, event.tool_call.args))

    hooks.register("message_end", _on_message_end)
    hooks.register("tool_execution_start", _on_tool_start)

    engine = Engine(cwd=cwd, llm=llm, tools=tools, system_prompt=system_prompt, hooks=hooks)
    ctx = EngineContext(
        system_prompt=system_prompt,
        messages=[UserMessage.from_text(f"Task: {task_text}")],
        tools=tools,
    )
    signal = asyncio.Event()
    run_task = asyncio.ensure_future(engine.run(ctx, signal=signal))

    try:
        await asyncio.wait_for(asyncio.shield(run_task), timeout=timeout_s)
    except TimeoutError:
        signal.set()
        try:
            await asyncio.wait_for(run_task, timeout=_ABORT_GRACE_S)
        except TimeoutError:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
        return False, f"Task timed out after {timeout_s:.0f}s", usage
    except Exception as e:
        return False, f"Subagent failed: {e}", usage

    if failed:
        return False, error_message or final_text or "(no output)", usage
    return True, final_text or "(no output)", usage
