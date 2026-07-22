---
name: autoresearch-hooks
description: Author before.sh/after.sh hooks that run automation at autoresearch iteration boundaries — notifications, journals, git tags, priming the next attempt
---

Hooks are optional automation glue for an autoresearch session. They run
alongside `init_experiment` / `run_experiment` / `log_experiment`, not
instead of them, and the agent driving the loop never calls them directly —
it just sees whatever text they print folded into the next message as a
`[hook before.sh]` / `[hook after.sh]` note.

## When to reach for a hook

Only when something needs to happen *outside* the tool schema — a desktop
notification, a durable journal, a git tag, fetching outside context before
the next attempt. If the automation can just as well be a line in
`.auto/prompt.md` that the agent reads, prefer that; it's simpler and every
fresh agent sees it without depending on a script.

## Where they live and when they fire

```
.auto/hooks/before.sh
.auto/hooks/after.sh
```

* **`before.sh`** — before an iteration starts: on `/autoresearch`
  activation, and again right after `after.sh` at the end of every
  `log_experiment` call. Prospective work: prime context, fetch research for
  the next attempt.
* **`after.sh`** — at the end of every `log_experiment` call. Retrospective
  work: journal what was learned, notify, tag the commit.

## Contract

* Must be executable — `chmod +x .auto/hooks/before.sh`. A file that exists
  but isn't executable is treated as absent, not an error.
* **stdin**: one line of JSON. Parse it with `jq`.
  * `before.sh` gets `{event, cwd, next_run, last_run, session}` (`last_run`
    is `null` on the very first activation).
  * `after.sh` gets `{event, cwd, run_entry, session}`.
  * `session` on both: `{metric_name, metric_unit, direction,
    baseline_metric, best_metric, run_count, goal}`.
  * `last_run` / `run_entry`: `{run, status, metric, description, metrics?}`.
* **stdout**: up to 8 KiB, delivered to the agent as a note. Print nothing
  and nothing is shown.
* **Errors**: a non-zero exit or a timeout (30s) surfaces as a note
  describing the failure — it does not stop the loop.
* **Logging**: every fire appends a `{"type": "hook", ...}` line to
  `.auto/log.jsonl`, so you can audit hook activity without a separate log.
* The whole `.auto/` folder — hooks included — survives a `git revert`, since
  hooks live outside version control's view of the experiment (they're
  session state, not code under test).

## Writing one

1. Copy the closest example from `skills/autoresearch-hooks/examples/` into
   `.auto/hooks/` under the right name.
2. Adapt it — most of the work is picking fields out of the JSON on stdin
   with `jq` and deciding what (if anything) to print back.
3. `chmod +x` it.
4. Test it standalone before trusting it in the loop:
   ```bash
   echo '{"event":"after","cwd":".","run_entry":{"run":1,"status":"keep","metric":9.9,"description":"test"},"session":{"metric_name":"seconds","metric_unit":"s","direction":"lower","baseline_metric":12.5,"best_metric":9.9,"run_count":1,"goal":"demo"}}' | .auto/hooks/after.sh
   ```
5. Keep it fast. Hooks run inline with the loop; a slow one (up to the 30s
   timeout) stalls every iteration, not just the first.

## Examples shipped here

`skills/autoresearch-hooks/examples/`:

* `notify.sh` (`after.sh`) — native desktop notification when a run lands,
  color-coded by status.
* `journal.sh` (`after.sh`) — appends a one-line, human-readable entry to
  `.auto/learnings.md` for every kept or discarded run, so the history is
  skimmable without parsing JSON.

Copy whichever fits, or use both as a starting point for something else.
