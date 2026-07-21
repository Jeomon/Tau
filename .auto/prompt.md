# Autoresearch session: speed up tau TUI startup

## Objective
Reduce the wall-clock time from launching `tau` to a ready-to-type interactive
TUI (cold process: interpreter start + imports + `Runtime.create` +
`App.create`). Lower is better.

## Measuring it
- Command: `bash .auto/measure.sh`
- Metric: `METRIC seconds=<value>` — best-of-5 wall time of a fresh
  `.venv/bin/python .auto/bench_tui_startup.py` subprocess.
- `.auto/bench_tui_startup.py` builds a real `RuntimeConfig` (cwd = repo
  root, `persist_session=False`, `project_trusted=True`, `mode="interactive"`),
  calls `await Runtime.create(config)` then `await App.create(runtime)`,
  then calls `os._exit(0)` immediately (no teardown, no render/input loop —
  that would block forever on a real terminal).
- Runs as a **fresh process every time** on purpose: process startup + module
  import time is often the dominant cost for a CLI and is exactly the kind of
  thing worth optimizing (lazy imports, avoiding heavy imports at module
  scope, etc). Don't change the benchmark to reuse a warm interpreter unless
  the goal changes.
- `.auto/checks.sh` does not exist yet — there is no correctness gate wired
  up. Run `uv run pytest -q` (or a relevant subset) manually before keeping
  any change that touches non-trivial logic, and mention in the log/notes if
  you skipped it for a purely import-ordering change.

## Direction / metric
- name: seconds, unit: s, direction: lower

## Scope
- Fair game: anything under `tau/` that affects import time or the
  `Runtime.create` / `App.create` code paths (settings, LLM setup, session
  manager, resource loading, extension loading, TUI/theme/layout
  construction) — e.g. deferring imports, lazy-loading subsystems, caching
  expensive discovery (extensions, resources, themes), skipping unnecessary
  work when trust/config is already known.
- Out of scope / must not change: `.auto/measure.sh`,
  `.auto/bench_tui_startup.py` (except to fix a genuine benchmark bug — note
  it here if you do), test suite semantics, on-disk session format, public
  CLI flags/behavior for the end user (e.g. don't just skip real work to
  win the benchmark — the TUI must still come up correctly and pass
  `uv run pytest -q`).
- Be careful with anything touched by the (uncommitted-at-session-start)
  autoresearch extension itself under `.tau/extensions/autoresearch/` — it's
  the tool running this very loop, not the optimization target.

## Where startup time goes (from `tau/console/cli.py` + `tau --startup`)
`tau --startup` already prints per-phase timings for the `Runtime.create`
half (`settings`, `llm`, `session_manager`, `resources`, `extensions`,
`agent`) but stops before `App.create` (TUI/theme/layout construction),
which our benchmark also includes. Use `tau --startup` interactively for a
quick qualitative read on where backend time goes; use `.auto/measure.sh`
for the number that counts.

Likely areas worth investigating (not yet measured/tried):
- Heavy imports at module scope in `tau/runtime/types.py`,
  `tau/modes/interactive/app.py`, and their transitive imports (anthropic,
  google-genai, mistralai, openai, tiktoken, pygments, etc. — provider SDKs
  that may not be needed for the selected model/provider). Deferring
  provider-SDK imports until the provider is actually selected could be a
  big win if `LLM(...)` construction imports all of them eagerly.
- `tau/extensions/*` discovery/loading — does it scan and import every
  extension package (project-local `.tau/extensions` + user `~/.tau/extensions`)
  unconditionally, including ones irrelevant to this repo?
- `tau/resources/loader.py` — resource/context file discovery (AGENTS.md
  etc.) walking the filesystem.
- `tau/themes/registry.py` — theme discovery/loading cost.
- General: anything doing filesystem globbing, subprocess calls (git status
  is already backgrounded via `asyncio.to_thread`), or network-touching
  setup during startup.

## Tried so far
1. **baseline** (9e6fdbd): 0.8552s.
2. **benchmark fix** (d83fc14): 0.7056s (-17.5%). `bench_tui_startup.py` was
   using `asyncio.run()`, whose cleanup waits for `shutdown_default_executor`
   — including the `.tau/extensions/lsp` eager-warmup thread (a full project
   `os.walk`) fired fire-and-forget via `asyncio.ensure_future` on
   `runtime_ready`. That thread never actually blocks real TUI readiness, so
   counting it was a benchmark bug, not a real perf win. Fixed by using a
   bare event loop + `os._exit` instead. Keep this fix; don't revert it.
3. **lazy computer_use Desktop backend** (ba0278f): 0.5206s (-26.2% further,
   -39.1% vs original baseline). `.tau/extensions/computer_use/__init__.py`
   called `get_desktop_class()()` eagerly in `register()`, which on macOS
   imports PyObjC/Quartz/AppKit (~400ms) even though the tool is opt-in and
   most sessions never call it. Replaced with `_LazyDesktop`, a duck-typed
   proxy that defers the real backend's import/construction to the first
   `action='open'` call; `is_open` reports `False` cheaply until then. The
   platform-support check (previously implicit in the eager
   `except RuntimeError`) is kept via the cheap `get_platform_name()` call so
   unsupported platforms still skip registration. Verified: `pytest -q`
   (2653 passed) and a manual check that `Quartz` isn't in `sys.modules`
   until `.open()` is called.

Current best: 0.5206s (was 0.8552s before any real profiling — remember the
`bench` fix commit is a measurement correction, not an app change; the real
app-level win so far is computer_use, ~26% on top of the corrected number).


## Ideas not yet tried
- Profile actual import cost: `.venv/bin/python -X importtime .auto/bench_tui_startup.py 2> /tmp/importtime.txt`
  then `grep -a "^import time:" /tmp/importtime.txt | awk -F'|' '{gsub(/^ +| +$/,"",$2); print $2, $3}' | sort -rn | head -30`
  (2nd column is cumulative µs including children — that's the one that
  matters for "can we avoid importing this at all").
- Lazy-import provider SDKs (anthropic/openai/google-genai/mistralai/ollama)
  so only the active provider's SDK loads.
- Lazy-import `tiktoken`/`pygments`/`pylatexenc`/`rapidfuzz` etc. if only
  needed for specific features not touched at startup.
- Cache/memoize theme and extension discovery if it re-walks disk every run.
- Check whether `tau/builtins/tools.py` (`TOOLS`) eagerly imports every tool
  module (including ones with heavy deps) at import time vs. lazily.

## Baseline importtime findings (real numbers, this machine, 2025 run)
Top cumulative import costs from an actual baseline run (µs, cumulative
including children — see profiling command above):

- `_tau_ext_*.macos.desktop.service` / `.macos.ax` (~220ms / ~215ms): a
  **user-level** extension under `~/.tau/extensions` (likely `peer` or a
  macOS notification/accessibility integration) pulls in PyObjC
  (`Quartz`, `AppKit`, `Foundation`, `objc`, `CoreFoundation`) at import
  time — ~160-220ms just for that. This only reproduces on machines with
  that user extension installed, so treat it as a *pattern* to fix
  (extensions should not import heavy platform SDKs at module scope; defer
  until the feature is actually used) rather than something to hardcode
  around — don't special-case this repo's `~/.tau` contents.
- `git` (GitPython, ~220ms cumulative): imported eagerly at module scope by
  `tau/agent/prompt/builder.py` (`from tau.agent.prompt.builder import
  _git_status` in `tau/runtime/types.py`) even though the actual
  `_git_status()` call is already deferred to a background thread. The
  *import* itself is still synchronous and on the critical path. Deferring
  `import git` to inside `_git_status()` (or wherever it's first called)
  should shave a good chunk off with zero behavior change.
- `tau.extensions` / `tau.extensions.context` (~144ms / ~128ms) pulls in a
  user-level extension's web-search engine (`jina_engine`) which imports
  `httpx`/`h2`/`httpcore` (~137ms) at module scope, even though search is
  a tool invoked on demand, not at startup. Same pattern as above: fix by
  making extensions/tools lazy-import their heavy deps, not by touching
  `~/.tau` contents.
- `tau.modes.interactive.app` (~180ms cumulative) — check what it pulls in
  beyond the above; some of this overlaps with the extension costs since
  extension loading happens during `Runtime.create` before `App.create`.

Net takeaway: a good first experiment is deferring the `import git` in
`tau/agent/prompt/builder.py` / `tau/runtime/types.py` to inside the
function that uses it — safe, mechanical, and shows up directly in
`import time`. After that, look at whether `tau/extensions/*` loading
(project + user) can defer each extension module's *own* heavy imports
(that's a fix inside the extension files, or inside the loader if it can
enforce/encourage lazy imports) — but remember this repo's own
`tau/` source is the scope; `~/.tau/extensions` contents on this machine
are not something to edit as "the fix" since they won't ship with tau.
Focus optimization effort on `tau/` itself (e.g. does the extension
*loader* do anything that forces eager imports of extension internals it
doesn't need yet, like importing every submodule instead of just the
package's `register()` entry point?).
