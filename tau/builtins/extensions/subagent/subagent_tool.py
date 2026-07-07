"""Subagent tool — delegate tasks to specialized agents.

Spawns a separate `tau` process (in `--mode json` non-interactive mode) for
each subagent invocation, giving it an isolated context window. NDJSON hook
events on stdout (`message_end`, `tool_execution_start`, ...) are parsed back
into usage stats and a lightweight activity log.

Supports two execution modes (a single task is just a one-item list in
either), each optionally run in the background via `async=true`:
  - Spawn: {spawn: [{agent, task}, ...]}   concurrent, max 8, 4 at a time
  - Chain: {chain: [{agent, task}, ...]}   sequential, task may use '{previous}'

Foreground runs are ephemeral (no session saved). Background (`async=true`)
runs instead persist a session per run/step so they can later be interrupted
and resumed via action='status'/'interrupt'/'resume'.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import (
    AgentConfig,
    AgentScope,
    create_agent,
    delete_agent,
    disable_agent,
    discover_agents,
    eject_agent,
    enable_agent,
    reset_agent,
    update_agent,
)
from subagent_schema import AgentDefinition, SubagentParams

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


RunStatus = str  # "running" | "done" | "failed" | "interrupted"


@dataclass
class RunState:
    """A background (async=true) run, tracked for status/interrupt/resume.

    kind == "single": one subprocess, `result` holds its (live-mutating)
    SingleResult. kind == "chain": a sequence of subprocesses, one per step,
    appended to `chain_results` as they complete; `session_dir` holds each
    step's own session subdirectory so the most recent step can be resumed.
    """

    run_id: str
    kind: str  # "single" | "chain"
    agent: str  # agent name (single) or "chain" label
    cwd: Path
    session_dir: Path
    signal: asyncio.Event
    result: SingleResult | None = None
    chain_results: list[SingleResult] = field(default_factory=list)
    total_steps: int = 0
    current_step_dir: Path | None = None
    session_id: str | None = None
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    task_handle: asyncio.Task | None = None

    @property
    def target_result(self) -> SingleResult | None:
        """The result that reflects this run's most recent activity."""
        if self.kind == "chain":
            return self.chain_results[-1] if self.chain_results else None
        return self.result


_RUNS: dict[str, RunState] = {}


def _find_run(run_id: str) -> RunState | None:
    """Look up a run by exact id, or by unique prefix (like a git short hash)."""
    if run_id in _RUNS:
        return _RUNS[run_id]
    matches = [r for rid, r in _RUNS.items() if rid.startswith(run_id)]
    return matches[0] if len(matches) == 1 else None


def _format_run_line(run: RunState) -> str:
    elapsed = (run.ended_at or time.time()) - run.started_at
    label = run.agent if run.kind == "single" else f"chain ({run.total_steps} steps)"
    return f"{run.run_id} [{label}]: {run.status} ({elapsed:.1f}s)"


def _format_run_summary(run: RunState) -> str:
    elapsed = (run.ended_at or time.time()) - run.started_at
    lines = [f"run_id: {run.run_id}", f"status: {run.status}", f"elapsed: {elapsed:.1f}s"]

    if run.kind == "single":
        lines.append(f"agent: {run.agent}")
        if run.result is not None:
            usage = _format_usage(run.result.usage, run.result.model)
            if usage:
                lines.append(f"usage: {usage}")
            lines.append("---")
            lines.append(_truncate(run.result.output, 4000))
        return "\n".join(lines)

    lines.append(f"steps completed: {len(run.chain_results)}/{run.total_steps}")
    for r in run.chain_results:
        lines.append(f"  step {r.step}: {r.agent} — {'failed' if r.failed else 'ok'}")
    if run.chain_results:
        lines.append("---")
        lines.append(_truncate(run.chain_results[-1].output, 4000))
    return "\n".join(lines)


def start_background_single(
    default_cwd: Path,
    agents: list[AgentConfig],
    agent_name: str,
    task: str,
    cwd: str | None,
    *,
    main_model: str | None = None,
) -> RunState:
    run_id = uuid.uuid4().hex[:12]
    run_cwd = Path(cwd).expanduser().resolve() if cwd else default_cwd
    session_dir = Path(tempfile.mkdtemp(prefix=f"tau-subagent-{run_id}-"))
    run = RunState(
        run_id=run_id,
        kind="single",
        agent=agent_name,
        cwd=run_cwd,
        session_dir=session_dir,
        current_step_dir=session_dir,
        signal=asyncio.Event(),
    )
    _RUNS[run_id] = run

    agent = next((a for a in agents if a.name == agent_name), None)
    if agent is None:
        available = ", ".join(f'"{a.name}"' for a in agents) or "none"
        run.result = SingleResult(
            agent=agent_name,
            agent_source="unknown",
            task=task,
            exit_code=1,
            error_message=f'Unknown agent: "{agent_name}". Available agents: {available}.',
        )
        run.status = "failed"
        run.ended_at = time.time()
        return run

    result = SingleResult(agent=agent_name, agent_source=agent.source, task=task, model=agent.model)
    run.result = result
    args = _build_run_args(
        agent, task, run_cwd, session_dir=session_dir, main_model=main_model
    )

    async def _drive() -> None:
        await _run_process(args, result, run.signal, None)
        run.session_id = _resolve_session_id(session_dir)
        run.status = (
            "interrupted"
            if result.stop_reason == "abort"
            else ("failed" if result.failed else "done")
        )
        run.ended_at = time.time()

    run.task_handle = asyncio.ensure_future(_drive())
    return run


def start_background_chain(
    default_cwd: Path,
    agents: list[AgentConfig],
    steps: list[Any],
    *,
    main_model: str | None = None,
) -> RunState:
    run_id = uuid.uuid4().hex[:12]
    session_dir = Path(tempfile.mkdtemp(prefix=f"tau-subagent-{run_id}-"))
    run = RunState(
        run_id=run_id,
        kind="chain",
        agent="chain",
        cwd=default_cwd,
        session_dir=session_dir,
        signal=asyncio.Event(),
        total_steps=len(steps),
    )
    _RUNS[run_id] = run

    async def _drive() -> None:
        previous_output = ""
        for i, step in enumerate(steps):
            if run.signal.is_set():
                run.status = "interrupted"
                run.ended_at = time.time()
                return

            agent = next((a for a in agents if a.name == step.agent), None)
            if agent is None:
                available = ", ".join(f'"{a.name}"' for a in agents) or "none"
                run.chain_results.append(
                    SingleResult(
                        agent=step.agent,
                        agent_source="unknown",
                        task=step.task,
                        exit_code=1,
                        error_message=(
                            f'Unknown agent: "{step.agent}". Available agents: {available}.'
                        ),
                        step=i + 1,
                    )
                )
                run.status = "failed"
                run.ended_at = time.time()
                return

            step_dir = session_dir / f"step-{i + 1}"
            step_dir.mkdir(parents=True, exist_ok=True)
            task_text = step.task.replace("{previous}", previous_output)
            run_cwd = Path(step.cwd).expanduser().resolve() if step.cwd else default_cwd
            r = SingleResult(
                agent=step.agent,
                agent_source=agent.source,
                task=task_text,
                model=agent.model,
                step=i + 1,
            )
            run.chain_results.append(r)
            args = _build_run_args(
                agent, task_text, run_cwd, session_dir=step_dir, main_model=main_model
            )
            await _run_process(args, r, run.signal, None)
            run.session_id = _resolve_session_id(step_dir)
            run.current_step_dir = step_dir

            if r.failed:
                run.status = "interrupted" if r.stop_reason == "abort" else "failed"
                run.ended_at = time.time()
                return
            previous_output = r.final_text

        run.status = "done"
        run.ended_at = time.time()

    run.task_handle = asyncio.ensure_future(_drive())
    return run


def _tau_invocation() -> list[str]:
    exe = shutil.which("tau")
    if exe:
        return [exe]
    return [sys.executable, "-c", "from tau.console.cli import main; main()"]


def _truncate(output: str, cap: int) -> str:
    encoded = output.encode("utf-8")
    if len(encoded) <= cap:
        return output
    truncated = encoded[:cap].decode("utf-8", errors="ignore")
    omitted = len(encoded) - len(truncated.encode("utf-8"))
    return f"{truncated}\n\n[Output truncated: {omitted} bytes omitted.]"


async def _drain_stderr(
    proc: asyncio.subprocess.Process, result: SingleResult, cap: int = 4096
) -> None:
    assert proc.stderr is not None
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await proc.stderr.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            break
    result.stderr_tail = b"".join(chunks).decode("utf-8", errors="replace")[-cap:]


async def _read_ndjson(
    proc: asyncio.subprocess.Process,
    result: SingleResult,
    signal: AbortSignal | None,
    emit: Any,
) -> bool:
    """Read NDJSON events from stdout. Returns True if the abort signal fired."""
    assert proc.stdout is not None
    while True:
        read_task: asyncio.Future[Any] = asyncio.ensure_future(proc.stdout.readline())
        signal_task: asyncio.Future[Any] | None = (
            asyncio.ensure_future(signal.wait()) if signal is not None else None
        )
        waiters: set[asyncio.Future[Any]] = {read_task}
        if signal_task is not None:
            waiters.add(signal_task)
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if signal_task is not None and signal_task in done:
                return True
            line = read_task.result()
        finally:
            for t in waiters:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

        if not line:
            return False

        try:
            event = json.loads(line)
        except ValueError:
            continue
        _apply_event(event, result)
        emit()


def _apply_event(event: dict[str, Any], result: SingleResult) -> None:
    etype = event.get("type")

    if etype == "tool_execution_start":
        tool_call = event.get("tool_call") or {}
        result.items.append(
            DisplayItem(
                kind="tool_call", name=tool_call.get("name", ""), args=tool_call.get("args") or {}
            )
        )
        return

    if etype == "message_end":
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            return
        result.usage.turns += 1
        usage = message.get("usage") or {}
        result.usage.input_tokens += usage.get("input_tokens", 0)
        result.usage.output_tokens += usage.get("output_tokens", 0)
        result.usage.cache_read_tokens += usage.get("cache_read_tokens", 0)
        result.usage.cache_write_tokens += usage.get("cache_write_tokens", 0)
        result.usage.cost += (usage.get("cost") or {}).get("total", 0.0)
        if message.get("stop_reason"):
            result.stop_reason = message["stop_reason"]
        if message.get("error"):
            result.error_message = message["error"]

        text = "".join(
            c.get("content", "") for c in message.get("contents", []) if c.get("type") == "text"
        )
        if text:
            result.items.append(DisplayItem(kind="text", text=text))
            result.final_text = text


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


def _build_run_args(
    agent: AgentConfig,
    task: str,
    run_cwd: Path,
    *,
    session_dir: Path | None = None,
    main_model: str | None = None,
) -> list[str]:
    args = ["--mode", "json", "--quiet", "--cwd", str(run_cwd)]
    if session_dir is not None:
        # Session persistence is gated by project trust; a background run's cwd
        # may never have been interactively trusted (no TUI to prompt in --mode
        # json). --approve is required or the session silently never gets
        # written and resume has nothing to continue from.
        args += ["--session-dir", str(session_dir), "--approve"]
    else:
        args += ["--ephemeral"]
    model = main_model or agent.model
    if model:
        args += ["--model", model]
    if agent.tools:
        args += ["--tools", ",".join(agent.tools)]
    if agent.system_prompt.strip():
        args += ["--system", agent.system_prompt]
    args += ["--prompt", f"Task: {task}"]
    return args


def _resolve_session_id(session_dir: Path) -> str | None:
    """Find the .jsonl session file written into session_dir, if any."""
    matches = sorted(session_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0].stem if matches else None


async def _run_process(
    args: list[str],
    result: SingleResult,
    signal: AbortSignal | None,
    on_update: Any,
) -> None:
    """Spawn `tau` with args, stream NDJSON into result, and wait for it to exit."""
    invocation = _tau_invocation()

    try:
        proc = await asyncio.create_subprocess_exec(
            *invocation,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        result.exit_code = 1
        result.error_message = f"Failed to start subagent process: {e}"
        return

    def _emit() -> None:
        if on_update is not None:
            on_update(result)

    stderr_task = asyncio.ensure_future(_drain_stderr(proc, result))
    aborted = await _read_ndjson(proc, result, signal, _emit)
    if aborted:
        proc.kill()
    result.exit_code = await proc.wait()
    await asyncio.gather(stderr_task, return_exceptions=True)

    if aborted:
        result.stop_reason = "abort"
        result.error_message = result.error_message or "Subagent was aborted."

    _emit()


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
    main_model: str | None = None,
) -> SingleResult:
    """Run one agent to completion in the foreground (ephemeral, no session saved)."""
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

    result = SingleResult(
        agent=agent_name, agent_source=agent.source, task=task, model=agent.model, step=step
    )
    run_cwd = Path(cwd).expanduser().resolve() if cwd else default_cwd
    args = _build_run_args(agent, task, run_cwd, main_model=main_model)
    await _run_process(args, result, signal, on_update)
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
        parts.append(f"↑{usage.input_tokens}")
    if usage.output_tokens:
        parts.append(f"↓{usage.output_tokens}")
    if usage.cache_read_tokens:
        parts.append(f"R{usage.cache_read_tokens}")
    if usage.cache_write_tokens:
        parts.append(f"W{usage.cache_write_tokens}")
    if usage.cost:
        parts.append(f"${usage.cost:.4f}")
    if model:
        parts.append(model)
    return " ".join(parts)


def _result_summary(r: SingleResult) -> dict[str, Any]:
    return {
        "agent": r.agent,
        "source": r.agent_source,
        "status": "running" if r.exit_code == -1 else ("error" if r.failed else "ok"),
        "stop_reason": r.stop_reason,
        "step": r.step,
        "usage": _format_usage(r.usage, r.model),
    }


def _format_agent_line(a: AgentConfig) -> str:
    tools = ", ".join(a.tools) if a.tools else "default"
    model = a.model or "default"
    return f"{a.name} ({a.source}): {a.description} [tools={tools}, model={model}]"


def _format_agent_detail(a: AgentConfig) -> str:
    tools = ", ".join(a.tools) if a.tools else "(default toolset)"
    model = a.model or "(default model)"
    return (
        f"name: {a.name}\n"
        f"source: {a.source}\n"
        f"description: {a.description}\n"
        f"tools: {tools}\n"
        f"model: {model}\n"
        f"file: {a.file_path}\n"
        f"---\n{a.system_prompt}"
    )


def _split_tools(raw: str) -> list[str] | None:
    tools = [t.strip() for t in raw.split(",") if t.strip()]
    return tools or None


WRITE_ACTIONS = {"create", "update", "delete", "eject", "disable", "enable", "reset"}


async def execute_management(
    invocation: ToolInvocation,
    params: SubagentParams,
    default_cwd: Path,
    agents: list[AgentConfig],
    confirm_write: Any,
) -> ToolResult:
    action = params.action
    name = params.agent
    scope = params.target_scope

    def _ok(content: str) -> ToolResult:
        return ToolResult.ok(invocation.id, content, metadata={"mode": "management"})

    def _err(content: str) -> ToolResult:
        return ToolResult.error(invocation.id, content, metadata={"mode": "management"})

    if (
        action in WRITE_ACTIONS
        and scope == "project"
        and params.confirm_project_agents
        and not await confirm_write(action, name, scope)
    ):
        return _err(f"Canceled: '{action}' at project scope not approved.")

    if action == "status":
        if not params.run_id:
            if not _RUNS:
                return _ok("No tracked runs.")
            lines = [
                _format_run_line(r)
                for r in sorted(_RUNS.values(), key=lambda r: r.started_at, reverse=True)
            ]
            return _ok("\n".join(lines))
        run = _find_run(params.run_id)
        if run is None:
            return _err(f'Unknown run id: "{params.run_id}".')
        return _ok(_format_run_summary(run))

    if action == "interrupt":
        if not params.run_id:
            return _err("'interrupt' requires 'run_id'.")
        run = _find_run(params.run_id)
        if run is None:
            return _err(f'Unknown run id: "{params.run_id}".')
        if run.status != "running":
            return _ok(f'Run "{run.run_id}" is already {run.status}; nothing to interrupt.')
        run.signal.set()
        if run.task_handle is not None:
            await run.task_handle
        return _ok(f'Interrupted "{run.run_id}".\n' + _format_run_summary(run))

    if action == "resume":
        if not params.run_id or not params.message:
            return _err("'resume' requires 'run_id' and 'message'.")
        run = _find_run(params.run_id)
        if run is None:
            return _err(f'Unknown run id: "{params.run_id}".')

        if run.status == "running":
            run.signal.set()
            if run.task_handle is not None:
                await run.task_handle

        target = run.target_result
        if run.session_id is None or target is None:
            return _err(f'Run "{run.run_id}" has no resumable session.')

        session_dir = run.current_step_dir or run.session_dir
        run.signal = asyncio.Event()
        run.status = "running"
        target.exit_code = -1
        target.stop_reason = None
        target.error_message = None

        resume_args = [
            "--mode",
            "json",
            "--quiet",
            "--session-dir",
            str(session_dir),
            "--approve",
            "--resume",
            run.session_id,
            "--prompt",
            params.message,
        ]

        async def _drive_resume() -> None:
            await _run_process(resume_args, target, run.signal, None)
            run.session_id = _resolve_session_id(session_dir)
            run.status = (
                "interrupted"
                if target.stop_reason == "abort"
                else ("failed" if target.failed else "done")
            )
            run.ended_at = time.time()

        run.task_handle = asyncio.ensure_future(_drive_resume())
        await run.task_handle
        return _ok(f'Resumed "{run.run_id}".\n' + _format_run_summary(run))

    if action == "list":
        if not agents:
            return _ok("No agents configured.")
        sorted_agents = sorted(agents, key=lambda a: a.name)
        lines = [_format_agent_line(a) for a in sorted_agents]
        return ToolResult.ok(
            invocation.id,
            "\n".join(lines),
            metadata={
                "mode": "management",
                "action": "list",
                "agents": [
                    {
                        "name": a.name,
                        "source": a.source,
                        "description": a.description,
                        "tools": ", ".join(a.tools) if a.tools else "default",
                        "model": a.model or "default",
                    }
                    for a in sorted_agents
                ],
            },
        )

    if action in ("get", "eject") and not name:
        return _err(f"'{action}' requires 'agent'.")

    if action == "get":
        found = next((a for a in agents if a.name == name), None)
        if found is None:
            available = ", ".join(a.name for a in agents) or "none"
            return _err(f'Unknown agent: "{name}". Available: {available}.')
        return _ok(_format_agent_detail(found))

    if action == "eject":
        found = next((a for a in agents if a.name == name), None)
        if found is None:
            available = ", ".join(a.name for a in agents) or "none"
            return _err(f'Unknown agent: "{name}". Available: {available}.')
        try:
            path = eject_agent(default_cwd, scope, found)
        except FileExistsError:
            return _err(
                f'"{name}" already has a custom file at {scope} scope. '
                "Use action='update' instead."
            )
        return _ok(f'Ejected "{name}" to {path} (now editable, shadows the original).')

    if action == "create":
        if not name:
            return _err("'create' requires 'agent'.")
        cfg = params.config
        if cfg is None or not cfg.description or not cfg.system_prompt:
            return _err("'create' requires 'config' with 'description' and 'system_prompt'.")
        tools = _split_tools(cfg.tools) if cfg.tools else None
        try:
            path = create_agent(
                default_cwd, scope, name, cfg.description, cfg.system_prompt, tools, cfg.model
            )
        except FileExistsError:
            return _err(f"\"{name}\" already exists at {scope} scope. Use action='update' instead.")
        return _ok(f'Created "{name}" at {path}.')

    if action == "update":
        if not name:
            return _err("'update' requires 'agent'.")
        cfg = params.config or AgentDefinition()
        try:
            path = update_agent(
                default_cwd,
                scope,
                name,
                description=cfg.description,
                system_prompt=cfg.system_prompt,
                tools="__unset__" if cfg.tools is None else _split_tools(cfg.tools),
                model="__unset__" if cfg.model is None else (cfg.model or None),
            )
        except FileNotFoundError:
            return _err(
                f'No custom agent named "{name}" at {scope} scope. If it is currently defined '
                f"elsewhere, use action='eject' first, or action='create' to define it here."
            )
        return _ok(f'Updated "{name}" at {path}.')

    if action == "delete":
        if not name:
            return _err("'delete' requires 'agent'.")
        try:
            path = delete_agent(default_cwd, scope, name)
        except FileNotFoundError:
            return _err(f'No custom agent named "{name}" at {scope} scope.')
        return _ok(f'Deleted "{name}" ({path}).')

    if action == "disable":
        if not name:
            return _err("'disable' requires 'agent'.")
        changed = disable_agent(default_cwd, scope, name)
        return _ok(
            f'"{name}" is now disabled at {scope} scope.'
            if changed
            else f'"{name}" was already disabled at {scope} scope.'
        )

    if action == "enable":
        if not name:
            return _err("'enable' requires 'agent'.")
        changed = enable_agent(default_cwd, scope, name)
        return _ok(
            f'"{name}" is now enabled at {scope} scope.'
            if changed
            else f'"{name}" was not disabled at {scope} scope.'
        )

    if action == "reset":
        if not name:
            return _err("'reset' requires 'agent'.")
        file_removed, was_disabled = reset_agent(default_cwd, scope, name)
        if not file_removed and not was_disabled:
            return _ok(f'Nothing to reset for "{name}" at {scope} scope.')
        parts: list[str] = []
        if file_removed:
            parts.append("removed custom file")
        if was_disabled:
            parts.append("cleared disabled override")
        return _ok(f'Reset "{name}" at {scope} scope: {", ".join(parts)}.')

    return _err(f'Unknown action: "{action}".')


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    action = args.get("action")
    if action:
        if action in ("status", "interrupt", "resume"):
            target = f" {args['run_id']}" if args.get("run_id") else ""
            return call_line("subagent", f"{action}{target}")
        target = f" {args['agent']}" if args.get("agent") else ""
        return call_line("subagent", f"{action}{target}", f"[{args.get('target_scope', 'user')}]")

    scope = args.get("agent_scope", "user")
    async_suffix = " (async)" if args.get("async") else ""

    chain = args.get("chain") or []
    spawn = args.get("spawn") or []
    items, label = (chain, "chain") if chain else (spawn, "spawn")

    if len(items) == 1:
        item = items[0]
        preview = item["task"].replace("{previous}", "").strip()
        preview = preview[:60] + ("..." if len(preview) > 60 else "")
        return call_line("subagent", item["agent"] + async_suffix, f"[{scope}]") + [
            f"    {preview}"
        ]

    unit = "steps" if label == "chain" else "tasks"
    lines = call_line("subagent", f"{label}{async_suffix} ({len(items)} {unit})", f"[{scope}]")
    for i, item in enumerate(items[:3]):
        preview = item["task"].replace("{previous}", "").strip()
        preview = preview[:40] + ("..." if len(preview) > 40 else "")
        prefix = f"{i + 1}. " if label == "chain" else ""
        lines.append(f"    {prefix}{item['agent']} {preview}")
    if len(items) > 3:
        lines.append(f"    ... +{len(items) - 3} more")
    return lines


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

    if mode == "management" and metadata.get("action") == "list" and not opts.is_error:
        agents_meta = metadata.get("agents") or []
        if agents_meta:
            import textwrap

            check = style(theme.success if theme else None, "✓")
            out: list[str] = []
            for a in agents_meta:
                out.append(f"{check} {a['name']} ({a['source']})")
                for line in textwrap.wrap(a["description"], 76) or [a["description"]]:
                    out.append(f"    {line}")
                out.append(f"    [tools={a['tools']}, model={a['model']}]")
            return out

    if mode == "management":
        icon = "✗" if opts.is_error else "✓"
        color = (theme.error if opts.is_error else theme.success) if theme else None
        return [f"{style(color, icon)} {lines[0]}", *lines[1:]]

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
        out = [header, *lines[:COLLAPSED_ITEM_COUNT]]
        if len(lines) > COLLAPSED_ITEM_COUNT:
            out.append(style(theme.muted, "(Ctrl+O to expand)") if theme else "(Ctrl+O to expand)")
        if r.get("usage"):
            out.append(style(theme.muted, r["usage"]) if theme else r["usage"])
        return out

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    label = mode
    all_ok = ok_count == len(results)
    icon = "✓" if all_ok else "◐"
    icon_color = (theme.success if all_ok else theme.warning) if theme else None
    out = [f"{style(icon_color, icon)} {label} {ok_count}/{len(results)}"]
    for r in results:
        step_label = f"Step {r['step']}: " if r.get("step") else ""
        rc = "✓" if r.get("status") == "ok" else ("✗" if r.get("status") == "error" else "⏳")
        out.append(f"  {step_label}{r.get('agent', '')} {rc}")
    out.append(style(theme.muted, "(Ctrl+O to expand)") if theme else "(Ctrl+O to expand)")
    return out


class SubagentTool(Tool):
    def __init__(self, runtime_ref: Any) -> None:
        self._runtime_ref = runtime_ref
        from tau.settings.paths import get_config_dir

        user_agents_dir = get_config_dir(None) / "agents"
        super().__init__(
            name="subagent",
            description=(
                "Delegate tasks to specialized subagents with isolated context, or manage "
                "agent definitions. If unsure what agents exist, call with "
                "action='list' first. Execution: pass 'spawn' (list of {agent, task}) to "
                "run tasks concurrently (max 8, 4 at a time), or 'chain' (list of "
                "{agent, task}) to run steps one after another, where each step's task "
                "may contain '{previous}' for the prior step's output — a single task is "
                "just a one-item list in either field. Add async=true to either to return "
                "immediately with a run id instead of waiting for completion. Management: "
                "set 'action' instead (omit 'spawn'/'chain') to 'list', 'get', 'create', "
                "'update', 'delete', 'eject', 'disable', 'enable', or 'reset' an agent "
                "definition, or 'status'/'interrupt'/'resume' to check on, stop, or "
                "continue a background run started with async=true — see each field's "
                "description for details. Ships with 'scout' (fast read-only recon), "
                "'planner', 'reviewer', and 'worker' (full access) out of the box. Add "
                f"your own in {user_agents_dir}. To enable project-local agents in "
                ".tau/agents, set agent_scope='both' or 'project' (trusted repositories "
                "only)."
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

    async def _confirm_project_write(self, action: str, name: str | None, scope: str) -> bool:
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return True

        from tau.extensions.context import ExtensionContext

        ext_ctx = ExtensionContext.from_runtime(runtime)
        if not ext_ctx.has_ui:
            return True

        target = f' "{name}"' if name else ""
        return await ext_ctx.confirm(
            f"Modify project-local agent{target}?",
            f"action='{action}', scope='{scope}' (.tau/agents)\n\n"
            "This writes to a repo-controlled file. Only continue for trusted repositories.",
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

        agent_scope: AgentScope = params.agent_scope
        agents, project_agents_dir = discover_agents(default_cwd, agent_scope)
        main_model = _parent_model(self._runtime_ref)

        if params.action is not None:
            if params.spawn or params.chain:
                return ToolResult.error(
                    invocation.id,
                    "'action' is management-only — omit 'spawn'/'chain' when 'action' is set.",
                    metadata={"mode": "management"},
                )
            return await execute_management(
                invocation, params, default_cwd, agents, self._confirm_project_write
            )

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

        if agent_scope in ("project", "both") and params.confirm_project_agents:
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
            if params.run_async:
                run = start_background_chain(
                    default_cwd, agents, params.chain, main_model=main_model
                )
                return _ok(
                    f'Started background chain run "{run.run_id}" ({len(params.chain)} steps). '
                    f"Use action='status' with run_id='{run.run_id}' to check on it.",
                    "chain",
                    [],
                )
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

        if params.run_async:
            runs = [
                start_background_single(
                    default_cwd, agents, t.agent, t.task, t.cwd, main_model=main_model
                )
                for t in params.spawn
            ]
            lines = [f"{r.run_id}: {r.agent}" for r in runs]
            return _ok(
                "Started background runs:\n"
                + "\n".join(lines)
                + "\nUse action='status' with run_id to check on them.",
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
