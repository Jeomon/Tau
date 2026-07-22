# autoresearch

An autonomous optimisation loop: try an idea, measure it, keep what works,
revert what doesn't, repeat.

Ported from [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch),
itself inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch).

The extension is deliberately domain-agnostic — it knows how to run a command,
read a number out of it, record the decision, and draw the result. *What* to
optimise lives in `.auto/prompt.md` and the bundled `autoresearch-create`
skill. One extension serves test speed, bundle size, training loss, Lighthouse
scores, or anything else you can print as a number.

## Usage

```
/autoresearch <goal>       start a new session, or resume with <goal> as context
/autoresearch status       where the session stands
/autoresearch dashboard    fullscreen, scrollable view
/autoresearch off          hide the dashboard (the log is kept)
/autoresearch clear        delete .auto/log.jsonl and reset
```

`ctrl+shift+f` also opens the fullscreen dashboard, where the terminal
delivers that combination.

## Tools

| Tool | Purpose |
|------|---------|
| `init_experiment` | Name the session; declare the metric, its unit, and whether lower or higher wins. Called once — again only if the optimisation target itself changes, which starts a new segment with a fresh baseline. |
| `run_experiment` | Run the benchmark, time it, parse `METRIC name=value` lines from its output, then run `.auto/checks.sh` if the benchmark passed. Measures only — it never decides. |
| `log_experiment` | Record the commit, metric, keep/discard decision and a one-line description. This is what makes the run durable. |

The measure/decide split is the point: the agent reads the numbers and chooses,
so the same infrastructure works for any target.

## Session files

Everything lives under `.auto/` in the working directory.

| File | Purpose |
|------|---------|
| `log.jsonl` | Append-only: one config header per segment, one line per run |
| `prompt.md` | The living session document — objective, what's been tried, dead ends |
| `measure.sh` | The benchmark; prints `METRIC name=value` lines |
| `checks.sh` | *(optional)* correctness gate run after a passing benchmark |
| `config.json` | *(optional)* `{"max_experiments": 50}` |

A fresh agent with no memory can read `prompt.md` plus the tail of `log.jsonl`
and continue exactly where the last one stopped. The log is append-only so a
crash loses at most the run in flight.

## Confidence

After three runs the dashboard shows a confidence score: the best improvement
divided by the session's noise floor, estimated as the Median Absolute
Deviation of the segment's metric values. MAD rather than a standard deviation
because one thermal-throttled run should not redefine "normal".

| Score | Reading |
|-------|---------|
| ≥ 2.0× | the win is likely real |
| 1.0–2.0× | above the noise, but marginal |
| < 1.0× | inside the noise — re-run before building on it |

Advisory only. Nothing is auto-discarded; the agent still decides.

## Dashboard

```
🔬 autoresearch: Cut unit-test runtime

  Runs: 5  3 kept  1 discarded  1 crashed  (conf: 2.0×)
  Baseline: ★ seconds: 12.5s #1
  Progress: ★ seconds: 9.9s #5 (-20.8%)

  #   commit   ★ seconds  collect_ms  status    description
  ──────────────────────────────────────────────────────────────────
  1   a1b2c3d  12.5s      900         ✔ keep    baseline
  3   c3d4e5f  14.8s      910         ✖ discard parallel workers (contention)
  4   d4e5f6a  0s         —           ✖ crash   import cache — broke collection
  5   e5f6a7b  9.9s       610         ✔ keep    drop the redundant conftest import
```

Secondary metrics get their own columns when the terminal is wide enough, and
are dropped from the right before the description is squeezed.

## Bundled skill

`skills/autoresearch-create/` ships with the extension and is registered from
`manifest.json`'s `"skills"` field. It gathers the goal, command, metric and
files in scope, writes `.auto/prompt.md` and `.auto/measure.sh`, runs the
baseline, and starts iterating.

## Not ported

The browser dashboard (`/autoresearch export`), the `before.sh`/`after.sh`
hooks, and the `autoresearch-finalize` skill that splits a noisy branch into
independently reviewable ones.
