"""The three tools that drive the loop: init, run, log.

The split matters. ``run_experiment`` only measures — it never decides. The
agent reads the numbers, decides keep or discard, and records that with
``log_experiment``. Keeping the decision out of the tooling is what lets the
same infrastructure serve any optimization target.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.tool.render import call_line
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

from .hooks import run_hook
from .state import (
    Result,
    State,
    append_config,
    append_result,
    checks_path,
    compute_confidence,
    format_num,
    is_better,
    parse_metrics,
)

DEFAULT_TIMEOUT = 600
DEFAULT_CHECKS_TIMEOUT = 300
#: Enough for the agent to diagnose a failure without flooding its context.
OUTPUT_TAIL_LINES = 40


# ── Schemas ───────────────────────────────────────────────────────────────────


class InitParams(BaseModel):
    name: str = Field(
        ...,
        description='What this session optimizes, e.g. "Cut unit-test runtime".',
    )
    metric_name: str = Field(
        ..., description='Primary metric shown in the dashboard, e.g. "seconds", "val_bpb".'
    )
    metric_unit: str = Field(
        default="", description='Unit suffix: "s", "ms", "kb", or "" for unitless.'
    )
    direction: str = Field(
        default="lower", description='"lower" or "higher" — which way is an improvement.'
    )


class RunParams(BaseModel):
    command: str = Field(
        ..., description="Shell command to benchmark, e.g. 'bash .auto/measure.sh'."
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT,
        description=f"Kill the command after this long (default {DEFAULT_TIMEOUT}).",
    )
    checks_timeout_seconds: int = Field(
        default=DEFAULT_CHECKS_TIMEOUT,
        description=f"Kill .auto/checks.sh after this long (default {DEFAULT_CHECKS_TIMEOUT}).",
    )


class LogParams(BaseModel):
    commit: str = Field(..., description="Short git commit hash for this experiment.")
    metric: float = Field(..., description="Primary metric value. Use 0 for a crash.")
    status: str = Field(..., description='"keep", "discard", "crash" or "checks_failed".')
    description: str = Field(..., description="One line on what this experiment changed.")
    metrics: dict[str, float] = Field(
        default_factory=dict, description='Secondary metrics, e.g. {"compile_ms": 420}.'
    )


# ── Shared helpers ────────────────────────────────────────────────────────────


def _tail(text: str, lines: int = OUTPUT_TAIL_LINES) -> str:
    parts = text.strip().splitlines()
    if len(parts) <= lines:
        return "\n".join(parts)
    return "\n".join(["…", *parts[-lines:]])


async def _run(command: str, cwd: Path, timeout: int) -> tuple[int | None, str, float]:
    """Run a shell command, capturing combined output. Returns (code, output, seconds).

    A timeout returns ``None`` as the code — the caller reports it as a crash
    rather than a benchmark result, since a killed run has no valid measurement.
    """
    started = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        code: int | None = proc.returncode
    except TimeoutError:
        proc.kill()
        stdout, _ = await proc.communicate()
        code = None
    return code, (stdout or b"").decode(errors="replace"), time.monotonic() - started


# ── Tools ─────────────────────────────────────────────────────────────────────


class InitExperimentTool(Tool):
    """Opens a session (or a new segment when the target changes)."""

    def __init__(self, session: Any) -> None:
        self._session = session
        super().__init__(
            name="init_experiment",
            description=(
                "Start an autoresearch session: name it, and declare the primary metric, "
                "its unit, and whether lower or higher is better. Call once before the "
                "first run_experiment. Call again only if the optimization target itself "
                "changes (different benchmark, metric or workload) — that starts a new "
                "segment with a fresh baseline and keeps the old results for reference."
            ),
            schema=InitParams,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=lambda args, streaming=False: call_line(
                "init_experiment", str(args.get("name", ""))
            ),
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = InitParams.model_validate(invocation.params)
        cwd = self._session.cwd
        state: State = self._session.state

        reinit = bool(state.results)
        if reinit:
            state.segment += 1
        state.name = params.name
        state.metric_name = params.metric_name
        state.metric_unit = params.metric_unit
        state.direction = "higher" if params.direction == "higher" else "lower"
        state.secondary = []

        append_config(cwd, state)
        self._session.refresh()

        arrow = "↓ lower is better" if state.direction == "lower" else "↑ higher is better"
        note = (
            "New segment started — earlier runs are kept for reference but the baseline resets."
            if reinit
            else "Session ready. Run the baseline next."
        )
        return ToolResult.ok(
            invocation.id,
            f"{state.name}\nMetric: {state.metric_name} ({arrow})\n{note}",
            metadata={"segment": state.segment},
        )


class RunExperimentTool(Tool):
    """Runs the benchmark and reports numbers. Decides nothing."""

    def __init__(self, session: Any) -> None:
        self._session = session
        super().__init__(
            name="run_experiment",
            description=(
                "Run a benchmark command and report how long it took plus any metrics it "
                "printed as 'METRIC name=value' lines. If .auto/checks.sh exists and the "
                "benchmark passed, it runs afterwards as a correctness gate. This tool only "
                "measures — decide keep or discard yourself, then record it with log_experiment."
            ),
            schema=RunParams,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=lambda args, streaming=False: call_line(
                "run_experiment", str(args.get("command", ""))
            ),
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = RunParams.model_validate(invocation.params)
        cwd = self._session.cwd
        state: State = self._session.state

        state.running_command = params.command
        state.running_since = time.time()
        self._session.refresh()
        try:
            code, output, seconds = await _run(params.command, cwd, params.timeout_seconds)
        finally:
            state.running_command = None
            state.running_since = None
            self._session.refresh()

        metrics = parse_metrics(output)
        lines = [
            f"$ {params.command}",
            f"exit: {'timeout' if code is None else code}    wall: {seconds:.2f}s",
        ]
        if metrics:
            lines.append("METRIC: " + "  ".join(f"{k}={format_num(v)}" for k, v in metrics.items()))
        else:
            lines.append(
                f"No METRIC lines found — print '{'METRIC'} name=value' from the benchmark, "
                "or log the wall-clock time as the metric."
            )

        if code is None:
            lines.append(f"Timed out after {params.timeout_seconds}s — log this as a crash.")
            return ToolResult.ok(
                invocation.id,
                "\n".join([*lines, "", _tail(output)]),
                metadata={"timed_out": True, "seconds": seconds, "metrics": metrics},
            )

        checks = checks_path(cwd)
        checks_ok: bool | None = None
        if code == 0 and checks.exists():
            checks_code, checks_output, _ = await _run(
                f"bash {checks.name}", checks.parent, params.checks_timeout_seconds
            )
            checks_ok = checks_code == 0
            if checks_ok:
                lines.append("checks.sh: passed")
            else:
                lines.append("checks.sh: FAILED — log this as checks_failed and revert.")
                lines.append(_tail(checks_output, 20))

        return ToolResult.ok(
            invocation.id,
            "\n".join([*lines, "", _tail(output)]),
            metadata={
                "exit_code": code,
                "seconds": seconds,
                "metrics": metrics,
                "checks_passed": checks_ok,
            },
        )


class LogExperimentTool(Tool):
    """Appends the decision to the log and refreshes the dashboard."""

    def __init__(self, session: Any) -> None:
        self._session = session
        super().__init__(
            name="log_experiment",
            description=(
                "Record one experiment in .auto/log.jsonl: the commit, the metric, whether "
                "you kept or discarded it, and a one-line description of what it changed. "
                "Call this after every run_experiment — the log is what lets a fresh agent "
                "resume the session, and what the dashboard reads."
            ),
            schema=LogParams,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=lambda args, streaming=False: call_line(
                "log_experiment",
                f"{args.get('status', '')}: {args.get('description', '')}",
            ),
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = LogParams.model_validate(invocation.params)
        cwd = self._session.cwd
        state: State = self._session.state

        if params.status not in ("keep", "discard", "crash", "checks_failed"):
            return ToolResult.error(
                invocation.id,
                f"Unknown status {params.status!r} — use keep, discard, crash or checks_failed.",
            )

        result = Result(
            commit=params.commit,
            metric=params.metric,
            status=params.status,  # type: ignore[arg-type]
            description=params.description,
            metrics=dict(params.metrics),
            segment=state.segment,
        )
        # Confidence is computed *including* this result, then stored on it, so
        # a re-read shows what the agent saw when it made the call.
        state.results.append(result)
        result.confidence = compute_confidence(state.current(), state.direction)
        for name in result.metrics:
            if all(m.name != name for m in state.secondary):
                from .state import MetricDef

                state.secondary.append(MetricDef(name=name))

        append_result(cwd, result)
        self._session.refresh()

        baseline = state.baseline()
        best = state.best()
        lines = [f"Logged #{state.run_number(result)} — {params.status}: {params.description}"]

        if baseline is not None and baseline is not result and baseline.metric:
            pct = (result.metric - baseline.metric) / baseline.metric * 100
            better = is_better(result.metric, baseline.metric, state.direction)
            lines.append(
                f"{state.metric_name}: {format_num(result.metric, state.metric_unit)} "
                f"({pct:+.1f}% vs baseline — {'better' if better else 'worse'})"
            )
        if best is not None:
            lines.append(
                f"Best so far: {format_num(best.metric, state.metric_unit)} "
                f"(#{state.run_number(best)})"
            )
        if result.confidence is not None:
            verdict = (
                "likely real"
                if result.confidence >= 2.0
                else "marginal — consider re-running"
                if result.confidence >= 1.0
                else "within noise — re-run before trusting it"
            )
            lines.append(f"Confidence: {result.confidence:.1f}× ({verdict})")

        if state.max_experiments and len(state.current()) >= state.max_experiments:
            lines.append(
                f"Reached max_experiments ({state.max_experiments}). Stop here and summarise "
                "unless the user asks for more."
            )

        run_number = state.run_number(result)
        after_note = await run_hook(
            "after", cwd, state, last_result=result, last_run_number=run_number
        )
        if after_note:
            lines.append(f"\n[hook after.sh]\n{after_note}")
        before_note = await run_hook(
            "before",
            cwd,
            state,
            last_result=result,
            last_run_number=run_number,
            next_run_number=run_number + 1,
        )
        if before_note:
            lines.append(f"\n[hook before.sh]\n{before_note}")

        return ToolResult.ok(invocation.id, "\n".join(lines), metadata=result.to_json())


def build_tools(session: Any) -> list[Tool]:
    return [InitExperimentTool(session), RunExperimentTool(session), LogExperimentTool(session)]
