"""LoopTask model and on-disk scheduler state."""

from __future__ import annotations

import json
import random
import string
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from duration import FIFTEEN_MINUTES, ONE_MINUTE, THREE_DAYS, format_duration  # type: ignore[import-not-found]

MAX_TASKS = 50


@dataclass
class LoopTask:
    id: str
    prompt: str
    enabled: bool
    created_at: float
    next_run_at: float
    interval_s: float
    expires_at: Optional[float] = None
    jitter_s: float = 0
    last_run_at: Optional[float] = None
    last_status: Optional[str] = None
    run_count: int = 0
    pending: bool = False


def _format_relative(ts: float) -> str:
    delta = ts - time.time()
    if delta <= 0:
        return "due now"
    mins = round(delta / ONE_MINUTE)
    if mins < 60:
        return f"in {max(mins, 1)}m"
    hours = round(mins / 60)
    if hours < 48:
        return f"in {hours}h"
    days = round(hours / 24)
    return f"in {days}d"


def _hash_id(task_id: str) -> int:
    h = 2166136261
    for ch in task_id:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


class SchedulerState:
    def __init__(self) -> None:
        self.tasks: dict[str, LoopTask] = {}
        self.storage_path: Optional[Path] = None
        self.dispatching = False

    def set_storage(self, cwd: Path) -> None:
        path = Path(cwd) / ".tau" / "loop" / "scheduler.json"
        if path != self.storage_path:
            self.storage_path = path
            self._load()

    def _load(self) -> None:
        self.tasks.clear()
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text())
            now = time.time()
            for item in raw.get("tasks", []):
                if not item.get("id") or not item.get("prompt"):
                    continue
                item = {**item, "pending": False}
                task = LoopTask(**{k: v for k, v in item.items() if k in LoopTask.__dataclass_fields__})
                if task.expires_at and now >= task.expires_at:
                    continue
                self.tasks[task.id] = task
        except Exception:
            pass  # corrupted store — continue with empty in-memory state

    def _persist(self) -> None:
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "tasks": [asdict(t) for t in sorted(self.tasks.values(), key=lambda t: t.next_run_at)],
            }
            tmp = self.storage_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.storage_path)
        except Exception:
            pass  # best-effort persistence

    def _new_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        while True:
            candidate = "".join(random.choices(alphabet, k=8))
            if candidate not in self.tasks:
                return candidate

    def _jitter(self, task_id: str, interval_s: float) -> float:
        max_jitter = min(interval_s * 0.1, FIFTEEN_MINUTES)
        if max_jitter <= 0:
            return 0
        return _hash_id(task_id) % (int(max_jitter) + 1)

    def add(self, prompt: str, interval_s: float) -> LoopTask:
        task_id = self._new_id()
        now = time.time()
        jitter = self._jitter(task_id, interval_s)
        task = LoopTask(
            id=task_id,
            prompt=prompt,
            enabled=True,
            created_at=now,
            next_run_at=now + interval_s + jitter,
            interval_s=interval_s,
            expires_at=now + THREE_DAYS,
            jitter_s=jitter,
        )
        self.tasks[task_id] = task
        self._persist()
        return task

    def set_enabled(self, task_id: str, enabled: bool) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        task.enabled = enabled
        if not enabled:
            task.pending = False
        self._persist()
        return True

    def delete(self, task_id: str) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._persist()
            return True
        return False

    def clear(self) -> int:
        n = len(self.tasks)
        self.tasks.clear()
        self._persist()
        return n

    def format_list(self) -> str:
        if not self.tasks:
            return "No loops scheduled."
        lines = ["Scheduled loops:", ""]
        for task in sorted(self.tasks.values(), key=lambda t: t.next_run_at):
            state = "on" if task.enabled else "off"
            mode = f"every {format_duration(task.interval_s)}"
            nxt = _format_relative(task.next_run_at)
            last = _format_relative(task.last_run_at) if task.last_run_at else "never"
            status = task.last_status or "pending"
            preview = task.prompt if len(task.prompt) <= 72 else f"{task.prompt[:69]}..."
            lines.append(f"{task.id}  {state}  {mode}  next {nxt}")
            lines.append(f"  runs={task.run_count}  last={last}  status={status}")
            lines.append(f"  {preview}")
        return "\n".join(lines)
