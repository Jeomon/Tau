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
4. **lazy web-search-engine imports** (2168b39): 0.5552s (measured this
   pass, within noise of #3 — system load had risen; real fix confirmed by
   profiling regardless). `tau/builtins/extensions/web/engines/__init__.py`
   eagerly imported all 4 search backends (ddgs/exa/jina/tavily) though only
   one is ever configured. Deferred each into its own `_build_*` function +
   a module `__getattr__` (PEP 562) for back-compat attribute access.
   Confirmed via profiling: jina's `h2`/`hpack` (HTTP/2) import chain no
   longer appears at all. `pytest -k 'web or search or fetch or engine'`
   passes (102 passed).
5. **memoize `PackageManager.site_packages()`** (dd9edbf): 0.5436s. Every
   extension/resource with declared dependencies (`computer_use`, `sandbox`
   in this repo) independently spawned a fresh `python -c "import site；..."`
   subprocess to locate the *same* shared venv's site-packages dir, even on
   a pure dependency-cache hit. Memoized per venv per process (module-level
   dict). `site_packages()`'s subprocess cost dropped out of the top-40
   profile entirely; `_ensure_dependencies` call count dropped from 5/4 to
   5/2. `pytest -q` passes (2653 passed).
6. **thread each local-model-discovery backend** (fe0ae03): 0.4286s
   (-50% vs baseline!). `Runtime._start_local_model_discovery()` fires
   `register_all()` (ollama/lmstudio/vllm/llamacpp scans) via
   `asyncio.ensure_future` — fire-and-forget in *intent*, but as a bare
   asyncio task on the *caller's* event loop, so each backend's
   `httpx.AsyncClient()` construction (connection pool/transport setup,
   ~90-100ms each despite the SSL context already being shared/cached) ran
   inline on the same thread driving `Runtime.create`/`App.create`, i.e. the
   TUI's own startup. Changed to `asyncio.to_thread(asyncio.run, backend())`
   per backend — each now gets its own OS thread + its own fresh event loop,
   mirroring the existing git-status/LSP-warmup threading pattern. Confirmed
   via profiling: all 4 `httpx.AsyncClient.__init__` calls gone from the
   main-thread profile. `pytest -q` passes (2653 passed, incl.
   `tests/test_local_model_discovery.py`, whose `monkeypatch.setattr(httpx,
   "AsyncClient", ...)` still applies fine across threads since it mutates
   the shared module object).
7. **lazy builtin theme parsing** (c6bd75c): 0.3848s (-55% vs baseline!).
   `ThemeRegistry._ensure_builtins()` eagerly parsed+validated all 17
   builtin theme YAML files (~80ms of YAML parsing/color validation) on
   *every* startup via `App.create()`'s theme resolution, even though
   exactly one theme is ever selected. Changed to register one lazy,
   memoizing factory per file, keyed by filename stem — verified 1:1 against
   every builtin theme's declared `name:` field, so this is a pure fast
   path with no behavior change for any shipped theme. Only
   `load_external`/`load_paths` (global/project themes, rarely present)
   still parse eagerly — not yet touched, lower priority since most users
   have none. `pytest -q` passes (2653 passed) + manual check all 17
   builtins still resolve correctly through the lazy path.

8. **typing fix, no perf change** (bcb98b5): fixed a real mypy regression
   introduced by #6 (`Awaitable` -> `Coroutine`, since `asyncio.run()`
   requires the latter). Confirmed `mypy tau/` didn't regress (actually
   fixed a pre-existing unrelated count: 6->5 errors, unrelated file).
9. **computer_use: defer schema import past the enabled-check** (4f836f2):
   0.3879s (no measurable delta in *this* benchmark — computer_use happens
   to be enabled in this dev environment). `from .computer import
   ComputerTool` (and `.state`) were at module scope, so building
   `ComputerSchema`'s pydantic model (a dozen-plus fields, several enums)
   was paid on every startup even when `register()` immediately returns
   because the extension is disabled — which is the *default*
   (manifest.json: `"enabled": false`). Moved both imports to inside
   `register()`, after the enabled/platform checks. Verified with a manual
   harness (module import alone, and `register()` with `enabled=False`,
   never import `.computer`; `enabled=True` still registers correctly).
   Real win for the common case (extension present but off), invisible in
   this specific benchmark's config. `pytest -q` passes (2653 passed).

Current best (this benchmark's config): **0.3848-0.3879s**, down from the
0.8552s baseline — **~55% faster**. Remember commit #2 (benchmark fix) is a
measurement correction, not an app change, and commit #9 is a real fix that
doesn't show up numerically here because of this environment's specific
extension config — the stacked, *measured-here* app-level wins are #3, #4,
#5, #6, #7.

## Checked, same "enabled by default" pattern, did NOT change
- `.tau/extensions/sandbox/__init__.py` imports `.manager`/`.sandbox_tool`
  at module scope too, but its `enabled` default is `True` (unlike
  computer_use's `False`) — most real installs use it as-is, so deferring
  past the enabled-check would only help the minority who've explicitly
  disabled it. Lower value; left alone. If revisited, same technique as #9
  applies directly.


## Ideas not yet tried / next up
- **`tau/builtins/tools.py` (`TOOLS`)** and pydantic schema generation: the
  latest profile (after fixes #1-#8) shows ~350 dataclasses processed and
  ~47 pydantic `BaseModel` schemas built during startup (each tool's params
  schema). This is core, necessary work for tools that are always
  registered — not obviously avoidable without deferring tool
  registration itself (risky, large change, not attempted). Worth a closer
  look only if profiling later shows it's grown disproportionately; treat
  as low-priority/high-risk for now.
- **`.tau/extensions/sandbox/__init__.py`** shows ~0.11s cumulative in
  profiling, mostly from importing `tau.builtins.tools.terminal` (the
  terminal tool, which the default agent config already loads regardless of
  the sandbox extension) — likely not an *avoidable* extra cost, just where
  that shared import happens to land first. Not fixed; would need to trace
  whether terminal's own import chain has anything deferrable.
- **`load_external`/`load_paths`** in `tau/themes/registry.py` (global
  `~/.tau/themes/` and project `.tau/themes/` themes) still eagerly parse
  every file, unlike the builtins fix in #7. Most users have zero
  global/project theme files, so this is usually free — only worth fixing
  if a user's `~/.tau/themes/` grows large. Same lazy-factory technique
  from #7 would apply, but keying by filename stem isn't guaranteed for
  user-authored files (unlike verified-1:1 builtins), so it needs the
  "parse to find declared name" fallback path that wasn't needed for #7.
- Confirmed (don't re-chase): `httpx` import/construction cost is no
  longer front-loaded onto the main thread (fixed in #6); jina's `h2`/hpack
  chain no longer imports at all when unused (fixed in #4); `import git` in
  `tau/agent/prompt/builder.py` was already lazy, nothing to fix there.
- Re-run `.venv/bin/python -X importtime .auto/bench_tui_startup.py` and a
  cProfile pass (see commands below) after each change — cumulative
  import-time numbers found so far are specific to *this* environment
  (e.g. `~/.tau/extensions` contents like `peer`'s macOS AX integration) and
  won't reproduce identically elsewhere; always re-check before trusting an
  old number. As of #8 (0.3848-0.3924s), the profile is dominated by
  generic Python import machinery (`importlib`, `exec`, `__build_class__`)
  and pydantic/dataclass schema construction — i.e. we've picked off the
  clear app-level wins in this environment; further gains likely require
  either deferring tool/schema construction (risky) or are specific to
  whatever `~/.tau/extensions` happen to be installed on the machine
  actually running the benchmark (not portable fixes).

## Profiling commands (for the next fresh agent)
```bash
# import-time breakdown (cumulative µs, includes children)
.venv/bin/python -X importtime .auto/bench_tui_startup.py 2> /tmp/importtime.txt
grep -a "^import time:" /tmp/importtime.txt | awk -F'|' '{gsub(/^ +| +$/,"",$2); print $2, $3}' | sort -rn | head -30

# cProfile of the exact benchmarked path (main thread only — background
# to_thread work won't show up, which is correct: it isn't on the critical
# path; background *asyncio tasks* like local-model-discovery DO show up)
.venv/bin/python -c "
import cProfile, pstats, asyncio, sys, os
sys.path.insert(0,'.')
from pathlib import Path
async def main():
    from tau.modes.interactive.app import App
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig
    config = RuntimeConfig(cwd=Path('.').resolve(), persist_session=False, project_trusted=True, mode='interactive')
    runtime = await Runtime.create(config)
    await App.create(runtime)
pr = cProfile.Profile(); pr.enable()
loop = asyncio.new_event_loop(); loop.run_until_complete(main())
pr.disable()
f = open('/tmp/prof.txt','w')
pstats.Stats(pr, stream=f).sort_stats('cumulative').print_stats(40)
f.flush(); f.close(); os._exit(0)
" </dev/null >/tmp/out.txt 2>&1
cat /tmp/prof.txt
```

## Past findings already fixed (don't re-attempt these)
- ~~`import git` is eager in `tau/agent/prompt/builder.py`~~ — checked: it's
  already a lazy `from git import Repo` *inside* `_git_status()`, not at
  module scope. The earlier importtime entry attributing ~220ms to `git`
  was from the backgrounded `_git_status` thread actually executing during
  the profiled window, not a synchronous startup cost. Nothing to fix here.
- `.tau/extensions/computer_use` eager Desktop backend import → fixed
  (`_LazyDesktop`, see log #3).
- `tau/builtins/extensions/web/engines/__init__.py` eager import of all 4
  search backends → fixed (lazy `_build_*` + module `__getattr__`, see
  log #4) — real fix, but don't expect a big wall-clock number for it
  specifically; see the local-model-discovery note above for why.
- A **user-level** `~/.tau/extensions` install on this dev machine (a macOS
  accessibility/notification integration, package name hash
  `_tau_ext_4e0873...`) also eagerly imports PyObjC/Quartz/AppKit
  (~200-220ms). This is *not* part of the `tau` repo and won't reproduce on
  another machine/CI — don't chase it further here, but note the general
  pattern (extensions eagerly importing heavy platform SDKs at
  registration time) is exactly what the computer_use fix addressed, so if
  it recurs elsewhere the same lazy-proxy technique applies.
