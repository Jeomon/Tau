# loop

A single `/loop` command for scheduling recurring prompts in Tau.

Design (interval parsing, id-derived jitter, 3-day auto-expiry, idle-gated
dispatch, atomic disk persistence) but trimmed to exactly what's needed here:
one command, no one-time reminders, no TUI manager, no LLM-callable tool.

## Usage

```
/loop <task>                 recurring, default interval (10m)
/loop 5m <task>               recurring every 5m
/loop <task> every 2h         recurring every 2h
/loop <task> 5m               recurring every 5m (trailing duration)
/loop create <task> <duration> explicit form of the above (any duration position)
/loop list                    list all loops
/loop enable <id>             re-enable a disabled loop
/loop disable <id>            pause a loop without deleting it
/loop remove <id>             delete a loop
/loop clear                   delete all loops
```

Intervals accept short (`5m`, `2h`, `3d`) or word forms (`5 minutes`, `2 hours`).
Anything below 1 minute is rounded up; anything not on a minute boundary is
rounded to the nearest minute.

A loop only fires while Tau is idle between turns (never mid-turn), and
auto-expires 3 days after creation. Each loop's next-run time gets a small
id-derived jitter (up to 10% of its interval, capped at 15m) so multiple loops
don't all fire in lockstep.

Tasks persist to `.tau/loop/scheduler.json` in the project directory and
survive session restarts.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Entry point — `register(tau)`, the `/loop` command handler, session hooks |
| `duration.py` | Interval parsing/formatting (`parse_duration`, `parse_loop_args`, `format_duration`) |
| `state.py` | `LoopTask` dataclass and `SchedulerState` (in-memory tasks + disk persistence) |
| `dispatch.py` | Per-second ticker: marks due tasks pending, dispatches one when idle, updates the footer status slot |
