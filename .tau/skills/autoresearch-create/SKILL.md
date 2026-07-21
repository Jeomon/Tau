---
name: autoresearch-create
description: Set up an autonomous optimisation loop — gather the goal, write the session files, run the baseline, and start iterating
---

Set up an autoresearch session, then run the loop until interrupted.

## 1. Work out what to optimise

You need four things. Infer what you can from the repo and the conversation; ask
only for what you genuinely cannot determine. Use `ask_user` if it is available —
one call, grouped questions, not a stream of them.

| | |
|---|---|
| **Goal** | What gets better, in one sentence. "Cut unit-test wall time." |
| **Command** | How to measure it, reproducibly. `pnpm test`, `uv run train.py`, `pytest -q` |
| **Metric** | The number to move, its unit, and whether lower or higher wins |
| **Scope** | Which files may change — and which must not |

Prefer a benchmark that runs in under a couple of minutes. A slow benchmark
means few experiments, and few experiments means noise wins.

## 2. Write the session files

Create a branch first (`autoresearch/<short-goal>`), so the main branch stays clean.

**`.auto/measure.sh`** — the benchmark. It must:

- exit non-zero if the workload is broken, before printing any metric
- print `METRIC name=value` lines, one per metric, e.g. `METRIC seconds=12.4`
- be quiet otherwise; the tail of its output is what you will read on failure

```bash
#!/usr/bin/env bash
set -euo pipefail
start=$(date +%s.%N)
pytest -q > /tmp/ar-out.txt 2>&1
end=$(date +%s.%N)
echo "METRIC seconds=$(echo "$end - $start" | bc)"
```

**`.auto/prompt.md`** — the session document. This is what a *fresh agent with no
memory* reads to continue, so write it for that reader: objective, the exact
command, the metric and direction, files in scope, ideas not yet tried, and a
running list of what has been tried with the outcome. Keep it updated as you go —
it matters more than any summary you hold in context.

**`.auto/checks.sh`** *(optional)* — correctness gate. Runs after a passing
benchmark; a failure means the optimisation broke something and must be reverted.

## 3. Baseline

Call `init_experiment` with the name, metric, unit and direction. Then run the
benchmark unchanged and `log_experiment` the result with status `keep` and a
description of "baseline". Everything afterwards is measured against this.

## 4. Loop

Repeat until interrupted:

1. Pick the most promising untried idea from `.auto/prompt.md`.
2. Make the smallest change that tests it. One idea per experiment — two changes
   at once and you learn nothing about either.
3. Commit it, so every result maps to a diff you can revert or keep.
4. `run_experiment` with the benchmark command.
5. Decide: better than the baseline and checks passed → `keep`. Worse, or checks
   failed → revert the commit (`git revert --no-edit HEAD` or `git reset --hard HEAD~1`)
   and log it as `discard` / `checks_failed`. A crash or timeout → `crash`.
6. `log_experiment` with the commit, the metric, the status and one line on what
   you tried. Append the outcome to `.auto/prompt.md` too.
7. If confidence is below ~1×, the result is inside the noise — re-run before
   trusting it rather than building on it.

Do not stop to ask permission between iterations. Keep going until the user
interrupts, the ideas run out, or `max_experiments` is reached — then summarise
what worked, what didn't, and what you would try next.
