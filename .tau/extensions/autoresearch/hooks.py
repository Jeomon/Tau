"""``before.sh`` / ``after.sh`` — optional automation at iteration boundaries.

Hooks are transparent to the agent: the agent calls ``init_experiment`` /
``run_experiment`` / ``log_experiment`` and sees their results exactly as
before. Hooks run alongside, outside the tool schema, and hand back at most a
short text note that gets folded into what the agent reads next. The core
loop has no awareness of *what* a hook does — only that it may produce a
steer-worthy note or nothing at all.

Trigger timing:

* ``before.sh`` fires before an iteration starts: on ``/autoresearch``
  activation, and again right after ``after.sh`` at the end of every
  ``log_experiment`` call. Use it for prospective work — priming context or
  fetching research for the next attempt.
* ``after.sh`` fires at the end of every ``log_experiment`` call. Use it for
  retrospective work — journaling what was learned, notifying, tagging.

Contract:

* Must be executable (``chmod +x``); a non-executable file is treated as
  absent rather than an error, so a freshly copied example doesn't misfire.
* stdin is a single-line JSON object; shape depends on the stage.
* stdout (capped at 8 KiB) is handed back to the caller as a note.
* A non-zero exit or a timeout produces a note describing the failure rather
  than raising — a broken hook must not take the loop down with it.
* Every fire is appended to ``.auto/log.jsonl`` as a ``{"type": "hook", ...}``
  line, for observability without a dedicated hook log.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from .state import Result, State, auto_dir, log_path

HOOKS_DIRNAME = "hooks"
BEFORE_NAME = "before.sh"
AFTER_NAME = "after.sh"

#: Hooks are meant to be quick (a notification, an API call) — 30s is
#: generous without letting a stuck script stall the loop indefinitely.
HOOK_TIMEOUT = 30
MAX_STDOUT_BYTES = 8 * 1024

HookKind = Literal["before", "after"]


def hooks_dir(cwd: Path) -> Path:
    return auto_dir(cwd) / HOOKS_DIRNAME


def before_path(cwd: Path) -> Path:
    return hooks_dir(cwd) / BEFORE_NAME


def after_path(cwd: Path) -> Path:
    return hooks_dir(cwd) / AFTER_NAME


def _script_for(kind: HookKind, cwd: Path) -> Path:
    return before_path(cwd) if kind == "before" else after_path(cwd)


def _is_runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _session_payload(state: State) -> dict[str, Any]:
    baseline = state.baseline()
    best = state.best()
    return {
        "metric_name": state.metric_name,
        "metric_unit": state.metric_unit,
        "direction": state.direction,
        "baseline_metric": baseline.metric if baseline is not None else None,
        "best_metric": best.metric if best is not None else None,
        "run_count": len(state.current()),
        "goal": state.name,
    }


def _run_payload(result: Result, run_number: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run": run_number,
        "status": result.status,
        "metric": result.metric,
        "description": result.description,
    }
    if result.metrics:
        out["metrics"] = dict(result.metrics)
    return out


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_STDOUT_BYTES:
        return text
    return encoded[:MAX_STDOUT_BYTES].decode("utf-8", errors="ignore") + "\n…(truncated)"


def _append_hook_log(cwd: Path, kind: HookKind, **fields: Any) -> None:
    record = {"type": "hook", "hook": kind, "timestamp": time.time(), **fields}
    path = log_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


async def run_hook(
    kind: HookKind,
    cwd: Path,
    state: State,
    *,
    last_result: Result | None = None,
    last_run_number: int | None = None,
    next_run_number: int | None = None,
) -> str | None:
    """Run ``before.sh``/``after.sh`` if present and executable.

    Returns a note for the agent, or ``None`` when there is nothing to say
    (no script, or the script printed nothing). Never raises — a broken hook
    is reported as a note, not an exception.
    """
    script = _script_for(kind, cwd)
    if not _is_runnable(script):
        return None

    payload: dict[str, Any] = {"event": kind, "cwd": str(cwd)}
    if kind == "before":
        payload["next_run"] = next_run_number
        payload["last_run"] = (
            _run_payload(last_result, last_run_number)
            if last_result is not None and last_run_number is not None
            else None
        )
    else:
        payload["run_entry"] = (
            _run_payload(last_result, last_run_number)
            if last_result is not None and last_run_number is not None
            else None
        )
    payload["session"] = _session_payload(state)
    stdin_bytes = (json.dumps(payload) + "\n").encode("utf-8")

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        _append_hook_log(cwd, kind, ok=False, error=str(exc))
        return f"{script.name} failed to start: {exc}"

    timed_out = False
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(stdin_bytes), timeout=HOOK_TIMEOUT)
        code: int | None = proc.returncode
    except TimeoutError:
        timed_out = True
        proc.kill()
        stdout, _ = await proc.communicate()
        code = None

    elapsed = time.monotonic() - started
    text = _truncate((stdout or b"").decode(errors="replace")).strip()
    ok = (not timed_out) and code == 0
    _append_hook_log(
        cwd, kind, ok=ok, elapsed=round(elapsed, 3), exit_code=code, timed_out=timed_out
    )

    if timed_out:
        return f"{script.name} timed out after {HOOK_TIMEOUT}s — check it isn't blocking on input."
    if code != 0:
        detail = f":\n{text}" if text else "."
        return f"{script.name} exited {code}{detail}"
    return text or None
