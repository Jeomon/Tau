"""Subagent tool — delegate tasks to specialized agents.

Runs each subagent turn in-process via tau.agent.embedded.run_embedded_agent
— its own Engine/LLM/tools, fully isolated, no OS subprocess. Raw hook
events (`message_end`, `tool_execution_start`, ...) are consumed directly as
Python objects and folded into usage stats and a lightweight activity log,
same shape as before when they arrived as NDJSON.

Three actions:
  - list:  (default when 'spawn'/'chain' are omitted) list every agent
  - get:   {agent}                                    full detail on one agent
  - tasks: (implicit whenever 'spawn'/'chain' is set)  execute:
             Spawn: {spawn: [{agent, task}, ...]}   concurrent, max 8, 4 at a time
             Chain: {chain: [{agent, task}, ...]}   sequential, task may reference '{previous}'

Every run is ephemeral: no session is saved for the subagent run, and there
is no background/async mode — the tool call blocks until its subagent(s)
finish (or are aborted). Context defaults to 'fresh' (no history); 'fork'
additionally resumes the parent's current session as read-only context —
reads the parent's session file directly (tau.agent.embedded.load_fork_context)
and seeds the embedded agent's message history with it, so the child sees
the conversation so far without ever writing back to it.

Model selection is intentionally simple for now: every subagent inherits the
parent session's current model rather than picking a model per agent role.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import AgentConfig, discover_agents
from subagent_schema import SubagentParams

from tau.agent.embedded import TASK_TIMEOUT_S, load_fork_context, run_embedded_agent
from tau.hooks.engine import MessageEndEvent, ToolExecutionStartEvent
from tau.message.types import Role
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
from tau.utils.format import format_number

MAX_PARALLEL_TASKS = 8
MAX_CONCURRENCY = 4
PER_TASK_OUTPUT_CAP = 50 * 1024
COLLAPSED_ITEM_COUNT = 10


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    turns: int = 0


@dataclass
class DisplayItem:
    kind: str  # "text" | "tool_call"
    text: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class SingleResult:
    agent: str
    agent_source: str
    task: str
    exit_code: int = -1  # -1 = still running
    final_text: str = ""
    items: list[DisplayItem] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    stop_reason: str | None = None
    error_message: str | None = None
    stderr_tail: str = ""
    step: int | None = None

    @property
    def failed(self) -> bool:
        return self.exit_code not in (0, -1) or self.stop_reason in ("error", "abort")

    @property
    def output(self) -> str:
        if self.failed:
            return self.error_message or self.stderr_tail or self.final_text or "(no output)"
        return self.final_text or "(no output)"


def _truncate(output: str, cap: int) -> str:
    encoded = output.encode("utf-8")
    if len(encoded) <= cap:
        return output
    truncated = encoded[:cap].decode("utf-8", errors="ignore")
    omitted = len(encoded) - len(truncated.encode("utf-8"))
    return f"{truncated}\n\n[Output truncated: {omitted} bytes omitted.]"


def _parent_model(runtime_ref: Any) -> str | None:
    """Model id (or 'provider/model' shorthand) currently active in the parent session."""
    runtime = getattr(runtime_ref, "runtime", None) if runtime_ref is not None else None
    if runtime is None:
        return None

    from tau.extensions.context import ExtensionContext

    ctx = ExtensionContext.from_runtime(runtime)
    if not ctx.model_id:
        return None
    return f"{ctx.provider_id}/{ctx.model_id}" if ctx.provider_id else ctx.model_id


def _parent_session(runtime_ref: Any) -> tuple[str, Path] | None:
    """(session_id, session_dir) of the parent's current persisted session, if any.

    Returns None when there's nothing on disk to fork from: the parent
    session isn't persisted (e.g. its own run is ephemeral, or trust is
    still pending), or it has no session_id/session_file yet.
    """
    runtime = getattr(runtime_ref, "runtime", None) if runtime_ref is not None else None
    sm = getattr(runtime, "session_manager", None) if runtime is not None else None
    if sm is None or not sm.persist or sm.session_id is None or sm.session_file is None:
        return None
    return sm.session_id, sm.session_dir


_EXTENSION_TOOL_NAMES = ("web_search", "web_fetch")


def _extension_tools(runtime_ref: Any) -> list[Tool]:
    """Extension-contributed tools an embedded agent can't construct itself
    (web_search/web_fetch need a configured search engine) — borrowed as
    already-instantiated, already-configured instances from the parent
    session's own tool registry, if one is live. Returns [] when there's no
    runtime (e.g. a headless test harness) or the web extension isn't
    registered/enabled, in which case an agent that requests these tools
    simply doesn't get them, same as before this ever existed.
    """
    runtime = getattr(runtime_ref, "runtime", None) if runtime_ref is not None else None
    registry = getattr(getattr(runtime, "_context", None), "tool_registry", None)
    if registry is None:
        return []
    tools = (registry.get(name) for name in _EXTENSION_TOOL_NAMES)
    return [t for t in tools if t is not None]


def _split_model_shorthand(model: str | None) -> tuple[str | None, str | None]:
    """Parse a 'provider/model' shorthand string into (model_id, provider).

    Mirrors resolve_model() in tau/console/cli.py: a bare model id (no "/")
    is passed through with no provider guess.
    """
    if model and "/" in model:
        provider, _, model_id = model.partition("/")
        return model_id, provider
    return model, None


def _apply_hook_event(event: Any, result: SingleResult) -> None:
    """Fold one raw Engine hook event into result — same effect _apply_event
    (the old NDJSON-dict version) had, just reading attributes instead of
    dict keys since these are now real event objects, not parsed JSON."""
    if isinstance(event, ToolExecutionStartEvent):
        result.items.append(
            DisplayItem(
                kind="tool_call",
                name=event.tool_call.name,
                args=event.tool_call.args or {},
            )
        )
        return

    if isinstance(event, MessageEndEvent):
        message = event.message
        if getattr(message, "role", None) != Role.ASSISTANT:
            return
        result.usage.turns += 1
        result.usage.input_tokens += message.usage.input_tokens
        result.usage.output_tokens += message.usage.output_tokens
        result.usage.cache_read_tokens += message.usage.cache_read_tokens
        result.usage.cache_write_tokens += message.usage.cache_write_tokens
        result.usage.cost += message.usage.cost.total
        if message.stop_reason:
            result.stop_reason = str(message.stop_reason)
        if message.error:
            result.error_message = message.error

        text = message.text_content()
        if text:
            result.items.append(DisplayItem(kind="text", text=text))
            result.final_text = text


async def run_single_agent(
    *,
    default_cwd: Path,
    agents: list[AgentConfig],
    agent_name: str,
    task: str,
    cwd: str | None,
    step: int | None,
    signal: AbortSignal | None,
    on_update: Any,
    main_model: str | None,
    requested_context: str | None,
    parent_session: tuple[str, Path] | None,
    runtime_ref: Any = None,
) -> SingleResult:
    """Run one agent to completion.

    requested_context is the explicit per-task/step or run-level 'context'
    (whichever was set), or None to fall back to the agent's own frontmatter
    default, or "fresh" if neither says otherwise. "fork" resumes the
    parent's current session as read-only context, read directly from its
    session file via load_fork_context() — never written back regardless of
    what the child does.
    """
    agent = next((a for a in agents if a.name == agent_name), None)
    if agent is None:
        available = ", ".join(f'"{a.name}"' for a in agents) or "none"
        return SingleResult(
            agent=agent_name,
            agent_source="unknown",
            task=task,
            exit_code=1,
            error_message=f'Unknown agent: "{agent_name}". Available agents: {available}.',
            step=step,
        )

    context_mode = requested_context or agent.context or "fresh"

    if context_mode == "fork" and parent_session is None:
        return SingleResult(
            agent=agent_name,
            agent_source=agent.source,
            task=task,
            exit_code=1,
            error_message=(
                "context='fork' requested but there is no persisted parent session to "
                "fork from (the current session isn't saved to disk yet)."
            ),
            step=step,
        )

    model = main_model or agent.model
    result = SingleResult(
        agent=agent_name, agent_source=agent.source, task=task, model=model, step=step
    )
    run_cwd = Path(cwd).expanduser().resolve() if cwd else default_cwd
    model_id, provider = _split_model_shorthand(model)

    initial_messages = None
    if context_mode == "fork" and parent_session is not None:
        session_id, session_dir = parent_session
        initial_messages = load_fork_context(run_cwd, session_id, session_dir)

    def _on_event(event: Any) -> None:
        _apply_hook_event(event, result)
        if on_update is not None:
            on_update(result)

    ok, output, _usage = await run_embedded_agent(
        cwd=run_cwd,
        model_id=model_id,
        provider=provider,
        system_prompt=agent.system_prompt,
        tool_names=agent.tools,
        task_text=task,
        initial_messages=initial_messages,
        abort_signal=signal,
        extra_tools=_extension_tools(runtime_ref),
        on_event=_on_event,
        timeout_s=TASK_TIMEOUT_S,
    )
    result.final_text = output
    result.exit_code = 0 if ok else 1
    if not ok:
        result.error_message = result.error_message or output
        result.stop_reason = result.stop_reason or "error"
    if on_update is not None:
        on_update(result)
    return result


async def _map_with_concurrency(items: list[Any], concurrency: int, fn: Any) -> list[Any]:
    if not items:
        return []
    limit = max(1, min(concurrency, len(items)))
    results: list[Any] = [None] * len(items)
    next_index = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal next_index
        while True:
            async with lock:
                if next_index >= len(items):
                    return
                idx = next_index
                next_index += 1
            results[idx] = await fn(items[idx], idx)

    await asyncio.gather(*(worker() for _ in range(limit)))
    return results


def _format_usage(usage: Usage, model: str | None) -> str:
    parts: list[str] = []
    if usage.turns:
        parts.append(f"{usage.turns} turn{'s' if usage.turns != 1 else ''}")
    if usage.input_tokens:
        parts.append(f"↑{format_number(usage.input_tokens)}")
    if usage.output_tokens:
        parts.append(f"↓{format_number(usage.output_tokens)}")
    if usage.cache_read_tokens:
        parts.append(f"R{format_number(usage.cache_read_tokens)}")
    if usage.cache_write_tokens:
        parts.append(f"W{format_number(usage.cache_write_tokens)}")
    if usage.cost:
        parts.append(f"${usage.cost:.4f}")
    if model:
        parts.append(model)
    return " ".join(parts)


def _resolve_action(args: dict) -> str:
    action = args.get("action")
    if action:
        return action
    return "tasks" if (args.get("spawn") or args.get("chain")) else "list"


def _format_agent_line(a: AgentConfig) -> str:
    tools = ", ".join(a.tools) if a.tools else "default"
    model = a.model or "inherits session model"
    return f"{a.name} ({a.source}): {a.description} [tools={tools}, model={model}]"


def _format_agent_detail(a: AgentConfig) -> str:
    tools = ", ".join(a.tools) if a.tools else "(default toolset)"
    model = a.model or "(inherits the parent session's model)"
    return (
        f"name: {a.name}\n"
        f"source: {a.source}\n"
        f"description: {a.description}\n"
        f"tools: {tools}\n"
        f"model: {model}\n"
        f"file: {a.file_path}\n"
        f"---\n{a.system_prompt}"
    )


def _result_summary(r: SingleResult) -> dict[str, Any]:
    return {
        "agent": r.agent,
        "source": r.agent_source,
        "status": "running" if r.exit_code == -1 else ("error" if r.failed else "ok"),
        "stop_reason": r.stop_reason,
        "step": r.step,
        "usage": _format_usage(r.usage, r.model),
    }


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    action = _resolve_action(args)

    match action:
        case "list":
            return call_line("subagent", "list")
        case "get":
            return call_line("subagent", "get", args.get("agent") or "...")
        case _:
            pass

    chain = args.get("chain") or []
    spawn = args.get("spawn") or []
    items, label = (chain, "chain") if chain else (spawn, "spawn")

    if len(items) == 1:
        item = items[0]
        preview = item["task"].replace("{previous}", "").strip()
        preview = preview[:60] + ("..." if len(preview) > 60 else "")
        return call_line("subagent", item["agent"]) + [f"    {preview}"]

    unit = "steps" if label == "chain" else "tasks"
    lines = call_line("subagent", f"{label} ({len(items)} {unit})")
    for i, item in enumerate(items[:3]):
        preview = item["task"].replace("{previous}", "").strip()
        preview = preview[:40] + ("..." if len(preview) > 40 else "")
        prefix = f"{i + 1}. " if label == "chain" else ""
        lines.append(f"    {prefix}{item['agent']} {preview}")
    if len(items) > 3:
        lines.append(f"    ... +{len(items) - 3} more")
    return lines


def _render_markdown_body(text: str, theme: Any) -> list[str]:
    """Render a subagent's raw text output as markdown, same as web_fetch does
    for fetched pages — subagents (planner, worker, ...) routinely return
    headers/lists/code blocks that read better rendered than as raw source."""
    if theme is None or not text.strip():
        return text.splitlines() or [text]

    from tau.tui.markdown import render_markdown

    width = max(1, min(shutil.get_terminal_size(fallback=(100, 24)).columns, 100) - 4)
    return render_markdown(text, width, theme.markdown) or (text.splitlines() or [text])


def _render_result(content: str, opts: Any) -> list[str]:
    theme = opts.theme
    metadata = opts.metadata or {}
    mode = metadata.get("mode", "spawn")
    results = metadata.get("results") or []

    def style(role: Any, text: str) -> str:
        if theme is None or role is None:
            return text
        from tau.tui.style import apply_style

        return apply_style(role, text)

    lines = content.splitlines() or [content]

    match mode:
        case "list":
            agents_meta = metadata.get("agents") or []
            if agents_meta and not opts.is_error:
                import textwrap

                check = style(theme.success if theme else None, "✓")
                out: list[str] = []
                for a in agents_meta:
                    out.append(f"{check} {a['name']} ({a['source']})")
                    for line in textwrap.wrap(a["description"], 76) or [a["description"]]:
                        out.append(f"    {line}")
                    out.append(f"    [tools={a['tools']}, model={a['model']}]")
                return out
            icon = "✗" if opts.is_error else "✓"
            color = (theme.error if opts.is_error else theme.success) if theme else None
            return [f"{style(color, icon)} {lines[0]}", *lines[1:]]

        case "get":
            icon = "✗" if opts.is_error else "✓"
            color = (theme.error if opts.is_error else theme.success) if theme else None
            return [f"{style(color, icon)} {lines[0]}", *lines[1:]]

        case _:
            pass

    if len(results) <= 1:
        r = results[0] if results else {}
        icon = "✓" if r.get("status") == "ok" else ("✗" if r.get("status") == "error" else "⏳")
        color = (
            theme.success
            if (theme and r.get("status") == "ok")
            else (
                theme.error
                if (theme and r.get("status") == "error")
                else (theme.warning if theme else None)
            )
        )
        header = f"{style(color, icon)} {r.get('agent', '')} ({r.get('source', '')})"
        body = _render_markdown_body(content, theme) if not opts.is_error else lines
        out = [header, *body[:COLLAPSED_ITEM_COUNT]]
        if r.get("usage"):
            out.append(style(theme.muted, r["usage"]) if theme else r["usage"])
        return out

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    all_ok = ok_count == len(results)
    icon = "✓" if all_ok else "◐"
    icon_color = (theme.success if all_ok else theme.warning) if theme else None
    out = [f"{style(icon_color, icon)} {mode} {ok_count}/{len(results)}"]
    for r in results:
        step_label = f"Step {r['step']}: " if r.get("step") else ""
        rc = "✓" if r.get("status") == "ok" else ("✗" if r.get("status") == "error" else "⏳")
        line = f"  {step_label}{r.get('agent', '')} {rc}"
        if r.get("usage"):
            line += f" — {r['usage']}"
        out.append(line)
    return out


class SubagentTool(Tool):
    def __init__(self, runtime_ref: Any) -> None:
        self._runtime_ref = runtime_ref
        from tau.settings.paths import get_config_dir

        user_agents_dir = get_config_dir(None) / "agents"
        super().__init__(
            name="subagent",
            description=(
                "Delegate tasks to specialized subagents with isolated context, or "
                "inspect what agents are available. action='list' (default when "
                "'spawn'/'chain' are both omitted) lists every agent's name, source, "
                "description, tools, and model — call this first if unsure what agents "
                "exist. action='get' returns full detail, including the system prompt, "
                "for one agent named in 'agent'. action='tasks' (implicit whenever "
                "'spawn'/'chain' is set) executes: pass 'spawn' (list of {agent, task}) "
                "to run tasks concurrently — a single task is just a one-item list — max "
                "8, 4 at a time; pass 'chain' (list of {agent, task}) to run steps "
                "sequentially, where a step's task may contain '{previous}' for the "
                "prior step's output. Each subagent runs as its own process with its own "
                "context window and inherits the current session's model. Set 'context' "
                "('fresh' or 'fork') at the run level or per task/step to override an "
                "agent's default: 'fork' resumes the parent session's current "
                "conversation as read-only context — the subagent sees everything so "
                "far but never writes back to the parent session. 'planner', 'worker', "
                "and 'oracle' default to 'fork' (they benefit from seeing the "
                "conversation so far); other builtins default to 'fresh'. Ships with "
                "'scout' (fast read-only recon), 'researcher' (web research), 'planner', "
                "'context-builder' (requirements + code recon), 'oracle' (second opinion "
                "/ drift check), 'worker', 'reviewer', and 'delegate' (lightweight "
                f"full-access) out of the box. Add your own in {user_agents_dir}, or in "
                ".tau/agents for project-local agents (repo-controlled — the user is "
                "prompted for confirmation before one actually runs)."
            ),
            schema=SubagentParams,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Parallel,
            render_call=_render_call,
            render_result=_render_result,
            render_shell="default",
        )

    async def _confirm_project_agents(
        self, agents: list[AgentConfig], names: set[str], project_dir: Path | None
    ) -> bool:
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return True

        from tau.extensions.context import ExtensionContext

        ext_ctx = ExtensionContext.from_runtime(runtime)
        if not ext_ctx.has_ui:
            return True

        requested = [a for a in agents if a.name in names and a.source == "project"]
        if not requested:
            return True

        agent_names = ", ".join(a.name for a in requested)
        return await ext_ctx.confirm(
            "Run project-local agents?",
            f"Agents: {agent_names}\nSource: {project_dir}\n\n"
            "Project agents are repo-controlled. Only continue for trusted repositories.",
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = SubagentParams.model_validate(invocation.params)
        default_cwd = invocation.cwd or (context.cwd if context else None) or Path.cwd()

        agents, project_agents_dir = discover_agents(default_cwd)
        main_model = _parent_model(self._runtime_ref)
        parent_session = _parent_session(self._runtime_ref)

        action = params.action or ("tasks" if (params.spawn or params.chain) else "list")

        match action:
            case "list":
                sorted_agents = sorted(agents, key=lambda a: a.name)
                if not sorted_agents:
                    return ToolResult.ok(
                        invocation.id,
                        "No agents configured.",
                        metadata={"mode": "list", "agents": []},
                    )
                lines = [_format_agent_line(a) for a in sorted_agents]
                return ToolResult.ok(
                    invocation.id,
                    "\n".join(lines),
                    metadata={
                        "mode": "list",
                        "agents": [
                            {
                                "name": a.name,
                                "source": a.source,
                                "description": a.description,
                                "tools": ", ".join(a.tools) if a.tools else "default",
                                "model": a.model or "inherits session model",
                            }
                            for a in sorted_agents
                        ],
                    },
                )

            case "get":
                if not params.agent:
                    return ToolResult.error(
                        invocation.id, "action='get' requires 'agent'.", metadata={"mode": "get"}
                    )
                found = next((a for a in agents if a.name == params.agent), None)
                if found is None:
                    available = ", ".join(a.name for a in agents) or "none"
                    return ToolResult.error(
                        invocation.id,
                        f'Unknown agent: "{params.agent}". Available: {available}.',
                        metadata={"mode": "get"},
                    )
                return ToolResult.ok(
                    invocation.id, _format_agent_detail(found), metadata={"mode": "get"}
                )

            case _:
                pass

        has_chain = bool(params.chain)
        has_spawn = bool(params.spawn)
        mode_count = sum([has_chain, has_spawn])

        def _ok(content: str, mode: str, results: list[SingleResult]) -> ToolResult:
            return ToolResult.ok(
                invocation.id,
                content,
                metadata={"mode": mode, "results": [_result_summary(r) for r in results]},
            )

        def _err(content: str, mode: str, results: list[SingleResult]) -> ToolResult:
            return ToolResult.error(
                invocation.id,
                content,
                metadata={"mode": mode, "results": [_result_summary(r) for r in results]},
            )

        if mode_count != 1:
            available = ", ".join(f"{a.name} ({a.source})" for a in agents) or "none"
            return _err(
                f"Invalid parameters. Provide exactly one of 'spawn' or 'chain'.\n"
                f"Available agents: {available}",
                "spawn",
                [],
            )

        requested_names: set[str] = set()
        if params.chain:
            requested_names.update(s.agent for s in params.chain)
        if params.spawn:
            requested_names.update(t.agent for t in params.spawn)
        if not await self._confirm_project_agents(agents, requested_names, project_agents_dir):
            return _err("Canceled: project-local agents not approved.", "spawn", [])

        async def _stream(mode: str, results: list[SingleResult]) -> None:
            if tool_execution_update_callback is None:
                return
            preview = "\n\n".join(f"[{r.agent}] {r.output}" for r in results) or "(running...)"
            await tool_execution_update_callback(
                ToolResult.ok(
                    invocation.id,
                    preview,
                    metadata={"mode": mode, "results": [_result_summary(r) for r in results]},
                )
            )

        if has_chain:
            assert params.chain is not None
            results: list[SingleResult] = []
            previous_output = ""
            for i, step in enumerate(params.chain):
                task_text = step.task.replace("{previous}", previous_output)
                r = await run_single_agent(
                    default_cwd=default_cwd,
                    agents=agents,
                    agent_name=step.agent,
                    task=task_text,
                    cwd=step.cwd,
                    step=i + 1,
                    signal=signal,
                    on_update=lambda res: asyncio.ensure_future(_stream("chain", [*results, res])),
                    main_model=main_model,
                    requested_context=step.context or params.context,
                    parent_session=parent_session,
                    runtime_ref=self._runtime_ref,
                )
                results.append(r)
                if r.failed:
                    return _err(
                        f"Chain stopped at step {i + 1} ({step.agent}): {r.output}",
                        "chain",
                        results,
                    )
                previous_output = r.final_text
            return _ok(results[-1].final_text or "(no output)", "chain", results)

        assert has_spawn and params.spawn is not None
        if len(params.spawn) > MAX_PARALLEL_TASKS:
            return _err(
                f"Too many spawned tasks ({len(params.spawn)}). Max is {MAX_PARALLEL_TASKS}.",
                "spawn",
                [],
            )

        all_results: list[SingleResult] = [
            SingleResult(agent=t.agent, agent_source="unknown", task=t.task) for t in params.spawn
        ]

        async def _run_one(t: Any, index: int) -> SingleResult:
            r = await run_single_agent(
                default_cwd=default_cwd,
                agents=agents,
                agent_name=t.agent,
                task=t.task,
                cwd=t.cwd,
                step=None,
                signal=signal,
                on_update=lambda res, i=index: (
                    all_results.__setitem__(i, res),
                    asyncio.ensure_future(_stream("spawn", all_results)),
                ),
                main_model=main_model,
                requested_context=t.context or params.context,
                parent_session=parent_session,
                runtime_ref=self._runtime_ref,
            )
            all_results[index] = r
            await _stream("spawn", all_results)
            return r

        results = await _map_with_concurrency(params.spawn, MAX_CONCURRENCY, _run_one)

        if len(results) == 1:
            r = results[0]
            if r.failed:
                return _err(f"Agent {r.stop_reason or 'failed'}: {r.output}", "spawn", results)
            return _ok(r.final_text or "(no output)", "spawn", results)

        success_count = sum(1 for r in results if not r.failed)
        summaries = [
            f"### [{r.agent}] {'failed' if r.failed else 'completed'}\n\n"
            f"{_truncate(r.output, PER_TASK_OUTPUT_CAP)}"
            for r in results
        ]
        content = f"Spawn: {success_count}/{len(results)} succeeded\n\n" + "\n\n---\n\n".join(
            summaries
        )
        return _ok(content, "spawn", results)
