from __future__ import annotations

import asyncio
import fcntl
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_DURATION_RE = re.compile(r"^(\+?)(\d+)([smhd])$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclass
class ScheduledJob:
    id: str
    expression: str
    request: dict[str, Any]
    kind: str
    next_run: float
    interval_seconds: int | None = None
    enabled: bool = True
    last_run: float | None = None
    last_error: str | None = None


def _cron_values(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            step = int(raw_step)
            if step < 1:
                raise ValueError("Cron step must be positive.")
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            raw_start, raw_end = part.split("-", 1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(part)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron value outside {minimum}..{maximum}: {part}")
        values.update(range(start, end + 1, step))
    return values


def _next_cron(expression: str, after: float) -> float:
    fields = expression.split()
    if len(fields) != 6:
        raise ValueError("Cron schedules require six fields: second minute hour day month weekday.")
    seconds = sorted(_cron_values(fields[0], 0, 59))
    minutes = sorted(_cron_values(fields[1], 0, 59))
    hours = sorted(_cron_values(fields[2], 0, 23))
    days = _cron_values(fields[3], 1, 31)
    months = _cron_values(fields[4], 1, 12)
    weekdays = _cron_values(fields[5], 0, 7)
    day_wildcard = fields[3] == "*"
    weekday_wildcard = fields[5] == "*"
    if 7 in weekdays:
        weekdays.add(0)

    start = datetime.fromtimestamp(after, tz=UTC) + timedelta(seconds=1)
    start = start.replace(microsecond=0)
    for day_offset in range(367):
        date = (start + timedelta(days=day_offset)).date()
        cron_weekday = (date.weekday() + 1) % 7
        day_matches = date.day in days
        weekday_matches = cron_weekday in weekdays
        if day_wildcard:
            calendar_matches = weekday_matches
        elif weekday_wildcard:
            calendar_matches = day_matches
        else:
            calendar_matches = day_matches or weekday_matches
        if date.month not in months or not calendar_matches:
            continue
        for hour in hours:
            for minute in minutes:
                for second in seconds:
                    candidate = datetime(
                        date.year,
                        date.month,
                        date.day,
                        hour,
                        minute,
                        second,
                        tzinfo=UTC,
                    )
                    if candidate >= start:
                        return candidate.timestamp()
    raise ValueError("Cron schedule has no occurrence within the next year.")


def parse_schedule(expression: str, now: float | None = None) -> tuple[str, float, int | None]:
    """Return schedule kind, next timestamp, and optional repeat interval."""
    current = time.time() if now is None else now
    match = _DURATION_RE.fullmatch(expression.strip())
    if match:
        relative, amount, unit = match.groups()
        seconds = int(amount) * _DURATION_SECONDS[unit]
        if seconds < 1:
            raise ValueError("Schedule duration must be positive.")
        return (
            "once" if relative else "interval",
            current + seconds,
            None if relative else seconds,
        )
    if len(expression.split()) == 6:
        return "cron", _next_cron(expression, current), None
    try:
        parsed = datetime.fromisoformat(expression.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "Schedule must be an interval (5m), relative time (+10m), ISO timestamp, "
            "or six-field cron expression."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    timestamp = parsed.timestamp()
    if timestamp <= current:
        raise ValueError("One-shot schedule must be in the future.")
    return "once", timestamp, None


class SubagentScheduler:
    def __init__(self, manager: Any, cwd: Path, session_id: str) -> None:
        self._manager = manager
        self._store = cwd / ".tau" / "subagents" / "schedules" / f"{session_id}.json"
        self._jobs: dict[str, ScheduledJob] = {}
        self._task: asyncio.Task[None] | None = None
        self._load()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def add(self, expression: str, request: dict[str, Any]) -> ScheduledJob:
        kind, next_run, interval = parse_schedule(expression)
        job = ScheduledJob(
            id=str(uuid.uuid4())[:8],
            expression=expression,
            request=request,
            kind=kind,
            next_run=next_run,
            interval_seconds=interval,
        )
        self._jobs[job.id] = job
        self._save()
        return job

    def cancel(self, job_id: str) -> bool:
        removed = self._jobs.pop(job_id, None) is not None
        if removed:
            self._save()
        return removed

    def list_jobs(self) -> list[ScheduledJob]:
        return sorted(self._jobs.values(), key=lambda job: job.next_run)

    async def _run(self) -> None:
        while True:
            now = time.time()
            due = [job for job in self._jobs.values() if job.enabled and job.next_run <= now]
            for job in due:
                await self._fire(job)
            await asyncio.sleep(1)

    async def _fire(self, job: ScheduledJob) -> None:
        request = dict(job.request)
        request["run_in_background"] = True
        request["inherit_context"] = False
        request["resume"] = None
        try:
            response = await self._manager.rpc_spawn(request)
            job.last_error = None if response["success"] else response.get("error")
        except Exception as exc:
            job.last_error = str(exc)
        job.last_run = time.time()
        if job.kind == "once":
            self._jobs.pop(job.id, None)
        elif job.kind == "interval":
            assert job.interval_seconds is not None
            job.next_run = time.time() + job.interval_seconds
        else:
            job.next_run = _next_cron(job.expression, time.time())
        self._save()

    def _load(self) -> None:
        if not self._store.is_file():
            return
        try:
            raw = json.loads(self._store.read_text(encoding="utf-8"))
            self._jobs = {
                item["id"]: ScheduledJob(**item)
                for item in raw
                if isinstance(item, dict) and item.get("id")
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._jobs = {}

    def _save(self) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._store.with_suffix(".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            temporary = self._store.with_suffix(".tmp")
            temporary.write_text(
                json.dumps([asdict(job) for job in self._jobs.values()], indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._store)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
