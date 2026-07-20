# Development

Setting up, testing, and debugging Tau itself. For contribution rules — commit format, PR process, security reporting — see [CONTRIBUTING.md](../CONTRIBUTING.md); for the module-by-module layout, see [Project Structure](project-structure.md).

## Table of Contents

- [Setup](#setup)
- [Commands](#commands)
- [Testing](#testing)
- [Linting and Type Checking](#linting-and-type-checking)
- [Continuous Integration](#continuous-integration)
- [Diagnostics](#diagnostics)
- [Logging](#logging)
- [Performance Profiling](#performance-profiling)
- [Environment Variables](#environment-variables)

## Setup

**Prerequisites:** Python ≥ 3.12 and < 3.14, git, and `uv` (recommended) or `pip`.

```bash
git clone https://github.com/Jeomon/Tau.git
cd Tau
uv sync                       # Resolve from uv.lock
uv sync --all-extras --dev    # Everything, matching CI
```

With pip instead:

```bash
pip install -e .
```

Verify:

```bash
tau --print "Say hello"       # Requires a configured provider
tau doctor                    # No API call; checks the whole install
```

> **Python version split.** `.python-version` pins **3.13** for local work, while CI validates on **3.12** and `mypy.ini` targets `python_version = 3.12`. `pyproject.toml` allows `>=3.12,<3.14`. Code must work on 3.12; do not rely on 3.13-only syntax.

The build backend is setuptools, the entry point is `tau = "tau.console.cli:main"`, and direct dependencies are `==`-pinned deliberately (`[tool.uv] resolution = "highest"`) as a supply-chain measure. Do not loosen a pin without reason.

## Commands

```bash
tau                                # Run the TUI from your working copy
tau -p "Test prompt"               # Print mode
tau --debug                        # Enable debug logging
tau --startup                      # Print startup phase timings to stderr
tau --cwd /path/to/project         # Run against another directory
tau --ephemeral                    # Don't persist the session
tau --base-url http://localhost:8080  # Point a provider at a local/mock server
tau doctor                         # Diagnose the install
```

| Flag | Description |
|------|-------------|
| `--debug`, `-d` | Enable debug-level logging |
| `--startup` | Print per-phase startup timings to stderr |
| `--cwd`, `-c` | Set the working directory |
| `--ephemeral`, `-e` | Do not save the session |
| `--base-url` | Per-run provider base-URL override; not persisted |
| `--tools` | Comma-separated tool allowlist |
| `--version`, `-v` | Print the version and exit |

`--base-url` combined with `--tools` is the fastest way to exercise a provider adapter against a local stub without touching settings.

## Testing

```bash
python -m pytest                                  # Everything
uv run pytest                                     # Everything, in the uv env
python -m pytest -v                               # Verbose
python -m pytest tests/test_doctor.py             # One module
python -m pytest tests/test_agent_compaction.py -k compact   # One pattern
```

There is no Makefile, tox, nox, or test shell script — `pytest` is the whole runner. The entire pytest configuration is:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

### Layout

`tests/` is flat — 146 `test_*.py` modules, no subpackages, named after the module under test.

```text
tests/
├── conftest.py            # Single session-scoped autouse fixture
├── render_helpers.py      # Shared TUI render assertions
├── fixtures/
│   └── dummy_mcp_server.py
├── test_agent_compaction.py
├── test_doctor.py
├── test_engine_execution.py
├── test_session_manager.py
└── ...                    # 142 more
```

`conftest.py` defines exactly one fixture: a session-scoped autouse hook that forces the tiktoken encoding used by compaction to finish loading before any test runs, so token-count assertions are deterministic instead of racing the chars/4 fallback.

### Async tests

`pytest-asyncio` runs in its default **strict** mode — no `asyncio_mode` is configured — so an async test must carry `@pytest.mark.asyncio`. Most of the suite instead drives coroutines with `asyncio.run(...)` from sync tests, which is the dominant existing pattern. No custom markers are registered.

### No network, no API keys

No test requires a provider key or network access. Keys are injected with `monkeypatch.setenv` and removed with `monkeypatch.delenv(..., raising=False)`; provider SDK tests run against a mocked httpx transport. The only conditional skip in the suite is `tests/test_image_processing.py`, which skips when Pillow is absent.

Keep it that way: a new test that reaches the network will pass locally and fail in CI.

## Linting and Type Checking

```bash
ruff format tau/          # Format
ruff check tau/           # Lint
mypy tau/                 # Type check
pyright tau/              # Second type checker (CI enforces this too)
```

| Tool | Configuration | Notes |
|------|---------------|-------|
| ruff | `[tool.ruff]` in `pyproject.toml` | `target-version = "py312"`, `line-length = 100`, rules `E, F, I, UP, B, SIM`, double quotes |
| mypy | `mypy.ini` | `python_version = 3.12`; deliberately lenient |
| pyright | *none* | No `pyrightconfig.json` and no `[tool.pyright]` — runs on defaults |

`mypy.ini` is intentionally permissive: `disallow_untyped_defs` and `check_untyped_defs` are both off, `warn_return_any` is off. It also sets `ignore_errors = True` for ~43 first-party modules — mostly inference adapters and TUI/settings hot spots, including `tau.runtime.service`, `tau.runtime.types`, `tau.engine.service`, `tau.agent.service`, `tau.settings.manager`, and `tau.session.utils`. Editing one of those means mypy will not catch your mistakes there; lean on `pyright tau/` and the tests instead.

> Tau uses **ruff exclusively** for both linting and formatting. Black, pylint, isort, and flake8 are not dependencies — do not add editor config that invokes them.

Run all five checks before pushing, in CI's order:

```bash
ruff format --check tau/ && ruff check tau/ && mypy tau/ && pyright tau/ && python -m pytest
```

Note that lint and format commands target `tau/` only, not `tests/`.

## Continuous Integration

Two workflows live in `.github/workflows/`.

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push and PR to `main` | Format, lint, type check, test |
| `cd.yml` | Push of a `v*` tag | Build, publish to PyPI, cut a GitHub release |

**`ci.yml`** runs one job, `check-and-test`, on `ubuntu-latest` with a single-entry matrix of Python `3.12`:

```bash
uv sync --all-extras --dev
uv run ruff format --check tau/
uv run ruff check tau/
uv run mypy tau/
uv run pyright tau/
uv run pytest
```

**`cd.yml`** runs `uv build`, publishes via PyPI trusted publishing (no token), slices the matching version section out of `CHANGELOG.md` into release notes, and attaches the wheel and sdist to the release. Update `CHANGELOG.md` with a `## <version>` heading or the release notes come out empty.

## Diagnostics

### `tau doctor`

The primary triage tool. It performs no model call and needs no network for most checks.

```bash
tau doctor          # Human-readable report
tau doctor --json   # Machine-readable
tau doctor --fix    # Apply safe, reversible repairs
```

| Section | Checks |
|---------|--------|
| Settings | Global and project settings load cleanly; recovered issues |
| Auth | Credential store loads; per-provider configuration and OAuth token validity |
| Models | Each modality slot (`text`, `voice`, `speak`, `image`, `video`) resolves to a registered provider |
| Extensions | Manifest validity, dangling settings entries, missing declared paths, dependency install state |
| Sessions | Session storage readable; corrupt session files |
| Logs | Recent logs scanned for tracebacks |
| Environment | Version consistency, shadowed `tau` installs on `PATH`, Python ≥ 3.12, packages venv, git root |
| Packages | Enabled packages recorded as installed but missing from the venv |

`--fix` is conservative: it refreshes expired OAuth tokens, removes dangling extension entries, quarantines corrupt session files, and backs up an unparseable settings file before resetting it. It never edits settings values directly and never reinstalls packages.

Exit code is `1` if any check **fails**; warnings alone exit `0`. The Logs section scans at most 20 `*.log` files modified in the last 7 days and warns for each containing a traceback.

Regression tests live in `tests/test_doctor.py`, which imports the private check functions directly — a useful pattern when adding a check.

### Debugging techniques

```python
import logging

logger = logging.getLogger(__name__)
logger.debug("state=%s", state)   # Lazy formatting; prefer over f-strings in logs
```

Use `breakpoint()` for interactive debugging — but **not in interactive mode**, where the TUI owns the terminal. Drop to print mode first:

```bash
tau -p "trigger the code path" --debug
```

`print()` is equally unusable in interactive mode: the TUI strips stdout/stderr log handlers and installs a null last-resort handler so nothing corrupts the renderer. Log to the file and read it instead.

> There is no `/debug` slash command and no `/logs` command. The built-in commands are listed in [Sessions](sessions.md#session-commands).

## Logging

Logs are written per run to a single file:

```text
~/.tau/logs/<session-id>.log
```

The path is always global — it does not follow `--cwd` into a project-local `.tau/`. The format is `%(asctime)s %(levelname)s %(name)s: %(message)s`, and the root level defaults to `WARNING`.

```bash
tau --debug                                   # Raise the level to DEBUG
ls -t ~/.tau/logs/*.log | head -1             # Most recent log
tail -f "$(ls -t ~/.tau/logs/*.log | head -1)"
```

In interactive mode `--debug` output lands in the **file**, not the terminal: `basicConfig` installs a stderr handler at startup, and the TUI then removes every stream handler to protect the renderer.

> **A frozen TUI with a working agent is almost always a swallowed render exception.** Check `~/.tau/logs/<session-id>.log` first — the traceback will be there even though the screen never updated.

There is no `--log-level` flag and no `TAU_LOG*` environment variable. `--debug` is the only level control.

## Performance Profiling

Two complementary tools.

| Tool | Enable with | Scope | Output |
|------|-------------|-------|--------|
| Startup timing | `--startup` | Six startup phases, one-shot | stderr |
| Span profiling | `TAU_PROFILE=1` | Aggregate spans across the whole run | `~/.tau/logs/profile-<pid>-<timestamp>.log` |

### Startup timing

```bash
tau --startup -p "hello"
```

Prints to stderr under a `--- Startup Timings ---` header, one line per phase with its own delta and the running total. The phases, in order, are `settings`, `llm`, `session_manager`, `resources`, `extensions`, and `agent`. Use it to answer "why is startup slow".

### Span profiling

```bash
TAU_PROFILE=1 tau -p "read the README and summarize it"
cat ~/.tau/logs/profile-*.log
```

The variable is read **once at import time** and must equal exactly `"1"` — `true`, `yes`, and `0` all leave it disabled, and setting it after the process starts has no effect. When disabled, spans compile to a bare `yield` with no clock read and no lock, so instrumentation is free in normal runs.

The report is written at exit, sorted by total time descending:

```text
span                                            count     total_ms     avg_ms     min_ms     max_ms
```

Instrumented spans include `startup.*` (the six phases above), `extensions.discover`, `extensions.declared_skills`, `extension.load.<source>.<name>` with `.dependencies` / `.import` / `.register` sub-spans, `tool.<tool_name>`, `session.rewrite_file`, and `session.append_entry`. TUI spans are injected via `set_span_hook()` rather than imported, because `tau.tui` is a standalone package that must not import `tau.*` — a constraint enforced by `tests/test_tui_public_api.py`.

`TAU_PROFILE=1` also auto-enables the startup stopwatch and folds its phases into the aggregate report, unless `--startup` already started it — the clock is never reset mid-run. Note the asymmetry: `--startup` alone prints to stderr and writes no file.

To instrument new code:

```python
from tau.utils import profiling

with profiling.span("mymodule.expensive_step"):
    result = expensive_step()

async with profiling.aspan("mymodule.async_step"):
    result = await async_step()
```

Span accumulation is guarded by a lock, since session persistence runs via `asyncio.to_thread` and tool calls run concurrently.

## Environment Variables

Tau reads exactly one `TAU_`-prefixed variable:

| Variable | Values | Effect |
|----------|--------|--------|
| `TAU_PROFILE` | `"1"` | Enable span profiling and write a report at exit |

Extensions can bind their own variables through `tau.register_flag(name, type=..., env="MY_VAR", default=...)` and read them with `tau.get_flag(name)`.

Other variables Tau reads, by category:

| Category | Variables |
|----------|-----------|
| Credentials | `<PROVIDER>_API_KEY` (uppercased provider ID, e.g. `ANTHROPIC_API_KEY`) |
| Proxy | `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` and `npm_config_*` equivalents, read case-insensitively |
| Anthropic | `CLAUDE_CONFIG_DIR`, `ANTHROPIC_CLI_VERSION`, `CLAUDE_CODE_ENTRYPOINT` |
| Google | `GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS` |
| Terminal detection | `TERM`, `TERM_PROGRAM`, `COLORTERM`, `TMUX`, `KITTY_WINDOW_ID`, `GHOSTTY_RESOURCES_DIR`, `WEZTERM_PANE`, `ITERM_SESSION_ID`, `WT_SESSION` |

Settings values also support indirection: `"$MY_VAR"` resolves from the environment and `"!some command"` resolves from shell output. Both are memoized. `python-dotenv` is a dependency, so a `.env` file in the working directory is loaded (and `.env` is gitignored).

Proxy settings in `settings.json` take precedence over the environment. See [HTTP Proxy](http-proxy.md).

## Next Steps

- [Project Structure](project-structure.md) — module-by-module breakdown
- [Architecture](architecture.md) — system design
- [Python API](python-api.md) — embedding and driving Tau programmatically
- [Extensions](extensions.md) — building extensions
- [CONTRIBUTING.md](../CONTRIBUTING.md) — commit format and PR process
