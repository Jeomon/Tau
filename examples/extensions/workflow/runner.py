"""Workflow execution engine.

Runs phases in order; each phase runs one or more subagent tasks, either
sequentially (chaining {previous}/{results.<label>}) or concurrently
(`parallel: true`), optionally fanning out over a prior result via
`for_each`. Every task runs in-process via tau.agent.embedded.run_embedded_agent
(shared with the `subagent` tool) — its own Engine/LLM/tools, fully isolated,
no OS subprocess and no shared session or registry state with the parent —
so there is no LLM tool call involved in running a workflow itself, just in
the tasks it dispatches.

Any task failure aborts the run (fail-fast): a workflow is meant to be a
predictable, rerunnable pipeline, not a best-effort fan-out.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tau.agent.embedded import TASK_TIMEOUT_S, run_embedded_agent

from .model import WorkflowDef, WorkflowPhase, WorkflowTask

MAX_CONCURRENCY = 4
_PLACEHOLDER_RE = re.compile(r"\{(previous|item|results\.[^{}]+)\}")
_FOR_EACH_RE = re.compile(r"^\{(previous|results\.([^{}]+))\}$")


def render_template(text: str, *, previous: str, item: str | None, results: dict[str, str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key == "previous":
            return previous
        if key == "item":
            return item if item is not None else m.group(0)
        label = key[len("results.") :]
        return results.get(label, m.group(0))

    return _PLACEHOLDER_RE.sub(_sub, text)


def resolve_list(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, list):
        return [item if isinstance(item, str) else json.dumps(item) for item in data]
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def resolve_for_each_source(expr: str, previous: str, results: dict[str, str]) -> str:
    m = _FOR_EACH_RE.match(expr.strip())
    if not m:
        raise ValueError(f"for_each must be '{{previous}}' or '{{results.<label>}}', got: {expr!r}")
    if m.group(1) == "previous":
        return previous
    label = m.group(2)
    if label not in results:
        raise ValueError(f"for_each references unknown result label: {label!r}")
    return results[label]


@dataclass
class TaskResult:
    phase: str
    agent: str
    label: str
    task_text: str
    ok: bool = False
    output: str = ""
    error: str = ""
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


@dataclass
class WorkflowRunResult:
    ok: bool
    results: list[TaskResult] = field(default_factory=list)
    duration_s: float = 0.0
    error: str = ""


async def _run_agent_process(
    cwd: Path,
    model_id: str | None,
    provider: str | None,
    agent_cfg: Any,
    task_text: str,
    schema: dict[str, Any] | None = None,
    extra_tools: list[Any] | None = None,
    on_tool_start: Callable[[str], None] | None = None,
    timeout_s: float = TASK_TIMEOUT_S,
) -> tuple[bool, str, dict[str, Any]]:
    """Run one subagent task in-process, bounded by ``timeout_s``. See tau/agent/embedded.py."""
    return await run_embedded_agent(
        cwd=cwd,
        model_id=model_id,
        provider=provider,
        system_prompt=agent_cfg.system_prompt,
        tool_names=agent_cfg.tools,
        task_text=task_text,
        schema=schema,
        extra_tools=extra_tools,
        on_tool_start=on_tool_start,
        timeout_s=timeout_s,
    )


async def _map_with_concurrency(
    items: list[Any], concurrency: int, fn: Callable[[Any, int], Any]
) -> list[Any]:
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


async def run_workflow(
    wf: WorkflowDef,
    *,
    cwd: Path,
    model_id: str | None,
    provider: str | None,
    agents: list[Any],
    extra_tools: list[Any] | None = None,
    on_phase: Callable[[str], None] | None = None,
    on_task_start: Callable[[str, str, str], None] | None = None,
    on_task_end: Callable[[TaskResult], None] | None = None,
    on_tool_start: Callable[[str, str, str], None] | None = None,
) -> WorkflowRunResult:
    start = time.monotonic()
    results: list[TaskResult] = []
    named_results: dict[str, str] = {}
    previous = ""

    def _find_agent(name: str) -> Any:
        return next((a for a in agents if a.name == name), None)

    async def _run_one(
        phase: WorkflowPhase, task: WorkflowTask, label: str, text: str
    ) -> TaskResult:
        if on_task_start:
            on_task_start(phase.title, label, task.agent)
        agent_cfg = _find_agent(task.agent)
        if agent_cfg is None:
            available = ", ".join(sorted({a.name for a in agents})) or "none"
            r = TaskResult(
                phase=phase.title,
                agent=task.agent,
                label=label,
                task_text=text,
                ok=False,
                error=f'Unknown agent "{task.agent}". Available: {available}.',
            )
        else:
            tool_cb: Callable[[str], None] | None = None
            if on_tool_start is not None:
                notify_tool_start = on_tool_start
                tool_cb = lambda preview: notify_tool_start(phase.title, label, preview)  # noqa: E731
            ok, output, usage = await _run_agent_process(
                cwd,
                model_id,
                provider,
                agent_cfg,
                text,
                task.schema,
                extra_tools=extra_tools,
                on_tool_start=tool_cb,
            )
            r = TaskResult(
                phase=phase.title,
                agent=task.agent,
                label=label,
                task_text=text,
                ok=ok,
                output=output if ok else "",
                error="" if ok else output,
                turns=usage["turns"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost=usage["cost"],
            )
        if on_task_end:
            on_task_end(r)
        return r

    def _fail(msg: str) -> WorkflowRunResult:
        return WorkflowRunResult(
            ok=False, results=results, duration_s=time.monotonic() - start, error=msg
        )

    for phase in wf.phases:
        if on_phase:
            on_phase(phase.title)

        if phase.for_each:
            try:
                source_text = resolve_for_each_source(phase.for_each, previous, named_results)
            except ValueError as e:
                return _fail(f"{phase.title}: {e}")
            items = resolve_list(source_text)
            template = phase.tasks[0]
            base_label = template.label or phase.title.lower().replace(" ", "-")

            async def _one_item(
                item: str,
                idx: int,
                _phase: WorkflowPhase = phase,
                _tmpl: WorkflowTask = template,
                _base: str = base_label,
                _prev: str = previous,
            ) -> TaskResult:
                text = render_template(_tmpl.task, previous=_prev, item=item, results=named_results)
                return await _run_one(_phase, _tmpl, f"{_base}[{idx}]", text)

            if phase.parallel:
                phase_results = await _map_with_concurrency(items, MAX_CONCURRENCY, _one_item)
            else:
                phase_results = [await _one_item(it, i) for i, it in enumerate(items)]

            results.extend(phase_results)
            failed = next((r for r in phase_results if not r.ok), None)
            if failed:
                return _fail(f"{phase.title}/{failed.label}: {failed.error}")

            named_results[base_label] = json.dumps([r.output for r in phase_results])
            previous = "\n\n---\n\n".join(r.output for r in phase_results)
            continue

        if phase.parallel:

            async def _one_task(
                task: WorkflowTask, idx: int, _phase: WorkflowPhase = phase, _prev: str = previous
            ) -> TaskResult:
                label = task.label or f"{_phase.title.lower().replace(' ', '-')}-{idx + 1}"
                text = render_template(task.task, previous=_prev, item=None, results=named_results)
                return await _run_one(_phase, task, label, text)

            phase_results = await _map_with_concurrency(phase.tasks, MAX_CONCURRENCY, _one_task)
            results.extend(phase_results)
            failed = next((r for r in phase_results if not r.ok), None)
            if failed:
                return _fail(f"{phase.title}/{failed.label}: {failed.error}")
            for r in phase_results:
                named_results[r.label] = r.output
            previous = "\n\n---\n\n".join(r.output for r in phase_results)
            continue

        for idx, task in enumerate(phase.tasks):
            label = task.label or f"{phase.title.lower().replace(' ', '-')}-{idx + 1}"
            text = render_template(task.task, previous=previous, item=None, results=named_results)
            r = await _run_one(phase, task, label, text)
            results.append(r)
            if not r.ok:
                return _fail(f"{phase.title}/{label}: {r.error}")
            previous = r.output
            named_results[label] = r.output

    return WorkflowRunResult(ok=True, results=results, duration_s=time.monotonic() - start)
