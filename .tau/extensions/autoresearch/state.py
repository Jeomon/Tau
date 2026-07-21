"""Session state and the append-only experiment log.

Everything the loop needs to survive a restart, a context reset, or a fresh
agent lives on disk under ``.auto/`` in the working directory:

    .auto/log.jsonl   append-only: one config header per segment, one line per run
    .auto/prompt.md   the living session document (written by the skill/agent)
    .auto/measure.sh  the benchmark (written by the skill/agent)
    .auto/checks.sh   optional correctness gate run after a passing benchmark
    .auto/config.json optional { "working_dir": ..., "max_experiments": ... }

The log is append-only on purpose: it is the source of truth, it is readable
by a human mid-run, and a crashed process loses at most the run in flight.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Status = Literal["keep", "discard", "crash", "checks_failed"]
Direction = Literal["lower", "higher"]

AUTO_DIR = ".auto"
LOG_NAME = "log.jsonl"
PROMPT_NAME = "prompt.md"
MEASURE_NAME = "measure.sh"
CHECKS_NAME = "checks.sh"
CONFIG_NAME = "config.json"

#: Commands emit these so a benchmark can report more than a wall-clock time:
#: ``METRIC name=value``, one per line, anywhere in stdout/stderr.
METRIC_PREFIX = "METRIC"

#: Never let a metric name collide with dict internals.
_DENIED_METRIC_NAMES = frozenset({"__proto__", "constructor", "prototype"})


def auto_dir(cwd: Path) -> Path:
    return cwd / AUTO_DIR


def log_path(cwd: Path) -> Path:
    return auto_dir(cwd) / LOG_NAME


def prompt_path(cwd: Path) -> Path:
    return auto_dir(cwd) / PROMPT_NAME


def measure_path(cwd: Path) -> Path:
    return auto_dir(cwd) / MEASURE_NAME


def checks_path(cwd: Path) -> Path:
    return auto_dir(cwd) / CHECKS_NAME


def config_path(cwd: Path) -> Path:
    return auto_dir(cwd) / CONFIG_NAME


# ── Records ───────────────────────────────────────────────────────────────────


@dataclass
class Result:
    """One experiment. Appended to the log and never mutated afterwards."""

    commit: str
    metric: float
    status: Status
    description: str
    metrics: dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    segment: int = 0
    #: Confidence at the time this was logged — kept for post-hoc analysis, so
    #: a later re-read shows what the agent actually saw when it decided.
    confidence: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "result",
            "commit": self.commit,
            "metric": self.metric,
            "metrics": self.metrics,
            "status": self.status,
            "description": self.description,
            "timestamp": self.timestamp,
            "segment": self.segment,
            "confidence": self.confidence,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Result:
        return Result(
            commit=str(raw.get("commit", "")),
            metric=float(raw.get("metric", 0) or 0),
            status=raw.get("status", "discard"),
            description=str(raw.get("description", "")),
            metrics={k: float(v) for k, v in (raw.get("metrics") or {}).items()},
            timestamp=float(raw.get("timestamp", 0) or 0),
            segment=int(raw.get("segment", 0) or 0),
            confidence=raw.get("confidence"),
        )


@dataclass
class MetricDef:
    name: str
    unit: str = ""


# ── Session state ─────────────────────────────────────────────────────────────


@dataclass
class State:
    """In-memory view of the log, rebuilt by :func:`load` on startup."""

    name: str = ""
    metric_name: str = "metric"
    metric_unit: str = ""
    direction: Direction = "lower"
    results: list[Result] = field(default_factory=list)
    secondary: list[MetricDef] = field(default_factory=list)
    segment: int = 0
    max_experiments: int | None = None
    #: Set while a benchmark is in flight so the widget can show a spinner.
    running_command: str | None = None
    running_since: float | None = None

    # ── Derived ──────────────────────────────────────────────────────────

    def current(self) -> list[Result]:
        """Results in the active segment. A re-init starts a new segment so a
        changed benchmark never gets compared against the old baseline."""
        return [r for r in self.results if r.segment == self.segment]

    def baseline(self) -> Result | None:
        """The first run of the segment — what everything else is measured against."""
        for r in self.current():
            if r.metric > 0:
                return r
        return None

    def best(self) -> Result | None:
        """Best *kept* run of the segment."""
        best: Result | None = None
        for r in self.current():
            if r.status != "keep" or r.metric <= 0:
                continue
            if best is None or is_better(r.metric, best.metric, self.direction):
                best = r
        return best

    def counts(self) -> dict[str, int]:
        out = {"keep": 0, "discard": 0, "crash": 0, "checks_failed": 0}
        for r in self.current():
            if r.status in out:
                out[r.status] += 1
        return out

    def confidence(self) -> float | None:
        return compute_confidence(self.current(), self.direction)

    def run_number(self, result: Result) -> int:
        """1-based index within the whole log, matching the ``#N`` in the table."""
        try:
            return self.results.index(result) + 1
        except ValueError:
            return 0


def is_better(candidate: float, reference: float, direction: Direction) -> bool:
    return candidate < reference if direction == "lower" else candidate > reference


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def compute_confidence(results: list[Result], direction: Direction) -> float | None:
    """How the best improvement compares to this session's noise floor.

    Uses the Median Absolute Deviation of the segment's metric values as a
    robust noise estimate — a couple of wild outliers (a thermal-throttled run,
    a flaky benchmark) barely move it, where a standard deviation would swing.

    ``|best - baseline| / MAD``. Roughly: ≥2 means the win is likely real, 1–2
    is marginal, <1 is inside the noise. Advisory only — nothing is auto-discarded.

    ``None`` when there is too little data (<3 runs) or the values are identical
    (MAD of 0), because a ratio against zero noise says nothing.
    """
    usable = [r for r in results if r.metric > 0]
    if len(usable) < 3:
        return None

    values = [r.metric for r in usable]
    median = _median(values)
    mad = _median([abs(v - median) for v in values])
    if mad == 0:
        return None

    baseline = next((r.metric for r in usable), None)
    if baseline is None:
        return None

    best: float | None = None
    for r in usable:
        if r.status == "keep" and (best is None or is_better(r.metric, best, direction)):
            best = r.metric
    if best is None:
        return None

    return abs(best - baseline) / mad


# ── Metric parsing ────────────────────────────────────────────────────────────


def parse_metrics(output: str) -> dict[str, float]:
    """Pull ``METRIC name=value`` lines out of command output.

    Scanned anywhere in the stream rather than requiring a final line, so a
    benchmark can emit them as it goes and still print whatever else it likes.
    """
    found: dict[str, float] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith(METRIC_PREFIX):
            continue
        rest = stripped[len(METRIC_PREFIX) :].strip()
        if "=" not in rest:
            continue
        name, _, value = rest.partition("=")
        name = name.strip()
        if not name or name in _DENIED_METRIC_NAMES:
            continue
        try:
            found[name] = float(value.strip())
        except ValueError:
            continue
    return found


def format_num(value: float, unit: str = "") -> str:
    """Human-sized number. Thousands separators for big values, trimmed decimals
    for small ones, so a table column stays narrow and comparable."""
    if value != value:  # NaN
        return "—"
    if abs(value) >= 1000:
        text = f"{value:,.0f}"
    elif abs(value) >= 100:
        text = f"{value:.1f}"
    elif abs(value) >= 1:
        text = f"{value:.2f}"
    else:
        text = f"{value:.4f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{unit}" if unit else text


# ── Persistence ───────────────────────────────────────────────────────────────


def append_config(cwd: Path, state: State) -> None:
    """Write a config header, opening a new segment in the log."""
    path = log_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "config",
        "name": state.name,
        "metric_name": state.metric_name,
        "metric_unit": state.metric_unit,
        "direction": state.direction,
        "timestamp": time.time(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def append_result(cwd: Path, result: Result) -> None:
    path = log_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.to_json()) + "\n")


def load(cwd: Path) -> State:
    """Rebuild the session from the log. Unreadable lines are skipped, not fatal.

    A truncated final line (killed mid-write) must not take the session down —
    the whole point of the log is that it survives crashes.
    """
    state = State()
    path = log_path(cwd)
    if not path.exists():
        state.max_experiments = read_max_experiments(cwd)
        return state

    seen_config = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue

        if raw.get("type") == "config":
            if seen_config:
                state.segment += 1  # each header opens the next segment
            seen_config = True
            state.name = str(raw.get("name", "") or "")
            state.metric_name = str(raw.get("metric_name", "metric") or "metric")
            state.metric_unit = str(raw.get("metric_unit", "") or "")
            direction = raw.get("direction")
            if direction in ("lower", "higher"):
                state.direction = direction
        elif raw.get("type") == "result":
            result = Result.from_json(raw)
            # Trust the log's own segment when present; fall back to the
            # header count for logs written before segments existed.
            if "segment" not in raw:
                result.segment = state.segment
            state.results.append(result)

    for result in state.current():
        for name in result.metrics:
            if all(m.name != name for m in state.secondary):
                state.secondary.append(MetricDef(name=name))

    state.max_experiments = read_max_experiments(cwd)
    return state


def read_max_experiments(cwd: Path) -> int | None:
    path = config_path(cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = raw.get("max_experiments") if isinstance(raw, dict) else None
    return int(value) if isinstance(value, int | float) and value > 0 else None


def clear(cwd: Path) -> None:
    """Delete the log. The prompt and measure script are deliberately kept —
    they are the expensive artefacts, and a fresh start usually reuses them."""
    path = log_path(cwd)
    if path.exists():
        path.unlink()
