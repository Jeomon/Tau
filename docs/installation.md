# Installation

This page covers installing Tau, authenticating with an inference provider, and verifying the result. For a guided first session once install succeeds, see [Quickstart](quickstart.md).

## Requirements

| Requirement | Value |
|-------------|-------|
| Python | `>=3.12,<3.14` (declared in `pyproject.toml`) |
| Package name | `tau-coding-agent` |
| Command installed | `tau` |
| Credentials | An API key or OAuth subscription for at least one provider |

> **Python 3.14 is not supported.** The `requires-python` bound stops at `<3.14`. See [Troubleshooting](#uv-tool-install-fails-building-pyxclip) if your installer picks 3.14 anyway.

## Install Tau

### From PyPI

```bash
pip install tau-coding-agent      # installs the `tau` command
```

### With uv

```bash
uv tool install tau-coding-agent --python 3.13
```

Pin the interpreter explicitly — uv otherwise defaults to its newest managed Python, which may exceed Tau's supported range.

### From source

```bash
git clone https://github.com/jeomon/tau.git
cd tau
pip install -e .                  # editable install; code changes take effect immediately
```

### Verify

```bash
tau --version                     # prints the version string and exits
tau --help                        # lists all global options and subcommands
tau doctor                        # full configuration/credential/model health check
```

`tau doctor` is the fastest way to confirm a working install — it checks settings and auth file integrity, credential status, model and provider resolution, extensions, session storage, logs, and installed packages in one pass.

## Authentication

Tau resolves an API key for a provider in this order, stopping at the first hit:

1. A runtime override supplied for the current process.
2. The credential stored for that provider in `~/.tau/auth.json`.
3. The environment variable `<PROVIDER_ID>_API_KEY`.

The auth file therefore **wins over** the environment variable. Both are implemented in `tau/auth/manager.py`.

### Environment variables

The env var name is derived mechanically from the provider id: uppercase it and append `_API_KEY`. There is no hand-maintained list of names.

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # provider id: anthropic
export OPENAI_API_KEY=sk-...           # provider id: openai
export GOOGLE_API_KEY=...              # provider id: google
export GROQ_API_KEY=gsk_...            # provider id: groq
tau
```

Common providers and their default endpoints:

| Provider id | Env var | Default base URL |
|-------------|---------|------------------|
| `anthropic` | `ANTHROPIC_API_KEY` | SDK default |
| `openai` | `OPENAI_API_KEY` | SDK default |
| `google` | `GOOGLE_API_KEY` | SDK default (Gemini Developer API) |
| `mistral` | `MISTRAL_API_KEY` | SDK default |
| `groq` | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| `xai` | `XAI_API_KEY` | `https://api.x.ai/v1` |
| `nvidia` | `NVIDIA_API_KEY` | `https://integrate.api.nvidia.com/v1` |
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| `cerebras` | `CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` |
| `fireworks` | `FIREWORKS_API_KEY` | `https://api.fireworks.ai/inference/v1` |
| `ollama` | none required | `http://localhost:11434` |
| `lmstudio` | none required | `http://localhost:1234/v1` |
| `vllm` | none required | `http://localhost:8000/v1` |
| `llamacpp` | none required | `http://localhost:8080/v1` |

See [Inference Providers](inference-providers.md) for the complete provider list, and [Authentication](auth.md) for OAuth subscription providers.

### Google Vertex AI

The Vertex providers (`google-vertex`, `anthropic-vertex`, `openai-vertex`) use Google Cloud credentials rather than a `*_API_KEY` variable:

| Variable | Purpose |
|----------|---------|
| `GOOGLE_CLOUD_PROJECT` | Project id |
| `GCLOUD_PROJECT` | Fallback project id |
| `GOOGLE_CLOUD_LOCATION` | Region |
| `GOOGLE_CLOUD_API_KEY` | API key when not using Application Default Credentials |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a service-account JSON file |

### Credential file

Store credentials in `~/.tau/auth.json`. The file is always global — there is no project-local auth file.

```json
{
  "anthropic": { "type": "api_key", "key": "sk-ant-..." },
  "openai":    { "type": "api_key", "key": "sk-..." },
  "google":    { "type": "api_key", "key": "..." }
}
```

Manage it from the CLI instead of editing by hand:

```bash
tau auth list                       # list stored credentials with masked keys
tau auth status                     # per-provider credential state, including env fallbacks
tau auth set anthropic sk-ant-...   # store an API key
tau auth unset anthropic            # remove stored credentials
tau auth login github-copilot       # run an OAuth subscription login flow
tau auth logout github-copilot      # remove an OAuth credential
```

Inside an interactive session, `/login` and `/logout` do the same thing through a picker.

### Indirect key values

The `key` field is resolved through `tau/utils/secrets.py`, which supports three forms:

| Form | Example | Behavior |
|------|---------|----------|
| Literal | `"sk-ant-..."` | Used as-is |
| Environment reference | `"$MY_KEY"` or `"${MY_KEY}"` | Read from the environment at use time |
| Shell command | `"!op read 'op://vault/item/key'"` | Executed once, cached for the process lifetime |

```json
{
  "anthropic": { "type": "api_key", "key": "$ANTHROPIC_API_KEY" },
  "openai":    { "type": "api_key", "key": "!op read 'op://vault/item/key'" }
}
```

This keeps plaintext keys out of the file while still letting `tau auth` manage entries.

## Where Tau Stores Things

All paths are fixed relative to `~/.tau/` and `<cwd>/.tau/`. **No environment variable relocates them** — use the `--session-dir` flag if you need session files elsewhere.

```text
~/.tau/                    # global config directory
├── settings.json          # global settings
├── auth.json              # credentials (0600); always global
├── sessions/              # session JSONL files, organized by working directory
├── logs/                  # per-session logs: <session_id>.log
├── extensions/            # installed extensions
├── themes/                # installed themes
├── skills/                # installed skills
└── venv/                  # global package virtualenv

<project>/.tau/            # project config directory
├── settings.json          # project settings (loaded only when the project is trusted)
└── venv/                  # project-scoped package virtualenv (`tau install --local`)
```

## Test Your Setup

Run a single prompt end-to-end. If a response prints, credentials and model resolution both work:

```bash
tau --print "Say exactly: hello"    # one-shot; prints the reply and exits
```

Then open the full terminal UI:

```bash
cd /path/to/your/project
tau
```

Tau starts on `anthropic/claude-sonnet-4-6` unless a different model is saved in settings or passed on the command line. Use `/model` inside the session to switch.

## Uninstall

```bash
pip uninstall tau-coding-agent      # or: uv tool uninstall tau-coding-agent
```

This removes the `tau` command but leaves settings, credentials, and sessions in `~/.tau/`. Delete that directory to remove them too.

## Troubleshooting

Start with `tau doctor`. It reports every check as pass/warn/fail and exits non-zero if anything failed.

```bash
tau doctor              # report only
tau doctor --json       # machine-readable output
tau doctor --fix        # apply safe, reversible repairs
```

`--fix` handles only reversible cases: refreshing expired OAuth tokens, removing dangling extension entries, and quarantining corrupt session files into a `.corrupt/` subdirectory. It never rewrites `settings.json` or `auth.json` directly and never reinstalls packages.

### No models found

Confirm the key is exported under the exact derived name:

```bash
env | grep -i api_key       # check which provider keys are visible
tau auth status             # check what Tau itself resolves, env fallbacks included
```

The variable must be `<PROVIDER_ID>_API_KEY` in uppercase — `ANTHROPIC_API_KEY`, not `ANTHROPIC_KEY`.

### Provider connection errors

1. **Network** — can you reach the provider endpoint from this machine?
2. **Key validity** — is the key expired or revoked?
3. **Region** — is your location or IP blocked by the provider?
4. **Proxy** — Tau honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` (either case). Settings in `settings.json` take precedence over these. See [HTTP Proxy](http-proxy.md).

### Ollama connection issues

Ollama needs no API key but the service must be running at `http://localhost:11434`:

```bash
ollama serve
tau --provider ollama
```

Point at a different endpoint for one run with `--base-url`:

```bash
tau --provider ollama --base-url http://gpu-box.local:11434
```

### `uv tool install` fails building `pyxclip`

`pyxclip` (clipboard support) ships prebuilt wheels only up to Python 3.13 — its `pyo3` bindings do not yet support 3.14. `uv tool install` picks uv's newest managed Python by default, regardless of this project's `requires-python` bound, so the install falls back to a source build that fails:

```text
error: The configured Python interpreter version (3.14) is newer than PyO3's maximum supported version (3.13)
```

Pin the interpreter for the install:

```bash
uv tool install tau-coding-agent --python 3.13
```

Or set it once for your shell:

```bash
# macOS/Linux
export UV_PYTHON=3.13

# Windows (PowerShell), current session
$env:UV_PYTHON = "3.13"

# Windows (PowerShell), persisted
[Environment]::SetEnvironmentVariable("UV_PYTHON", "3.13", "User")
```

## Next Steps

- [Quickstart](quickstart.md) — run your first session
- [Usage Guide](usage.md) — interactive mode, slash commands, and sessions
- [CLI Reference](cli-reference.md) — every flag, subcommand, and run mode
- [Inference Providers](inference-providers.md) — full provider reference
- [Authentication](auth.md) — OAuth subscription logins
- [Settings](settings.md) — persistent configuration
