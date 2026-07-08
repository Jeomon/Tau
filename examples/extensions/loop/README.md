# loop

A single `/loop` command for scheduling recurring prompts in Tau.

Runtime design (interval parsing, id-derived jitter, 3-day auto-expiry,
idle-gated dispatch, atomic disk persistence) modeled on
[pi-scheduler](https://github.com/manojlds/pi-scheduler), trimmed to exactly
what's needed here: one command, no one-time reminders, no LLM-callable tool.

## Usage

```
/loop                         open the interactive loop manager
/loop <period> <task>         create directly, e.g. /loop 5m water the plants
/loop <task> every <period>   create directly, e.g. /loop water the plants every 2h
```

Bare `/loop` opens a picker listing every loop as `■/☐ id  every <period>  —  <task>`
(■ = enabled, ☐ = disabled).
Selecting one opens an action menu: **Enable/Disable**, **Edit instruction**
(multi-line editor, prefilled), **Edit duration** (reparsed with the same
interval syntax), **Delete**, or **Back**. The list also has **+ New loop**
and **Clear all loops** entries. In headless mode (no TUI), bare `/loop`
just prints the current list as text since there's no picker to drive.

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
| `__init__.py` | Entry point — `register(tau)`, the `/loop` command handler, the interactive picker/action-menu flow, session hooks |
| `duration.py` | Interval parsing/formatting (`parse_duration`, `parse_loop_args`, `format_duration`) |
| `state.py` | `LoopTask` dataclass and `SchedulerState` (in-memory tasks, edits, + disk persistence) |
| `dispatch.py` | Per-second ticker: marks due tasks pending, dispatches one when idle, updates the footer status slot |
