<div align="center">

  <img src="assets/wordmark.svg" alt="Tau" height="160">
  <br>
  <a href="https://pypi.org/project/tau-coding-agent/">
    <img src="https://img.shields.io/pypi/v/tau-coding-agent.svg" alt="PyPI version">
  </a>
  <a href="https://pepy.tech/project/tau-coding-agent">
    <img src="https://static.pepy.tech/badge/tau-coding-agent" alt="PyPI Downloads">
  </a>
  <a href="https://github.com/Jeomon/Tau/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
  <br>
  <a href="https://github.com/Jeomon/Tau/actions/workflows/ci.yml">
    <img src="https://github.com/Jeomon/Tau/actions/workflows/ci.yml/badge.svg" alt="CI status">
  </a>
  <a href="https://github.com/Jeomon/Tau/commits/main">
    <img src="https://img.shields.io/github/last-commit/Jeomon/Tau.svg" alt="Last commit">
  </a>

</div>

<br>

Tau is a Python-based coding agent harness, heavily inspired by [Pi](https://github.com/earendil-works/pi) created by [Mario Zechner](https://github.com/badlogic). It combines an interactive terminal UI, multiple model providers, persistent sessions, tool execution, and an extension system in one package.

> **Note:** There are several coding-agent projects also named
> "Tau," including at least one that is itself a Python port of Pi. This
> project (`tau`, [Jeomon/Tau](https://github.com/Jeomon/Tau)) was built
> independently, taking inspiration only from the original
> [Pi](https://github.com/earendil-works/pi) project. No other "Tau" project,
> or any other Pi port, was referenced or used in its development.

<p align="center">
  <img src="assets/tui.jpeg" alt="Tau interactive terminal interface" width="700">
</p>

## Quick start

Requires Python 3.12+.

```bash
pip install tau-coding-agent
export NVIDIA_API_KEY=nvapi-...
tau --provider nvidia
```

Then ask Tau to work in the current directory:

```text
Explain this repository, run its tests, and fix any failures.
```

**Other providers:** pass `--model <provider>/<model>` with the matching API
key set, e.g. `GOOGLE_API_KEY=... tau --model google/gemini-2.5-flash`.

## Commands

### CLI usage

```bash
tau [OPTIONS] [MESSAGE]
```

```bash
tau                                      # Start an interactive session
tau --resume                             # Resume the latest session
tau --resume abc123                      # Resume a specific session by ID
tau --model claude-sonnet-4-6            # Start with a specific model
tau --model groq/llama-3.3-70b-versatile # provider/model shorthand
tau --print "Summarize this repository"  # Run once and print the result
tau --mode json --prompt "Summarize this repo"  # Emit structured JSON events
tau --mode rpc                           # Start JSON-RPC mode for IDE clients
tau --ephemeral                          # Temporary session, nothing saved
```

Common flags:

| Flag | Short | Description |
|---|---|---|
| `--prompt TEXT` | `-p` | Run a non-interactive prompt |
| `--print` | | Print mode — run `MESSAGE` and exit (shorthand for `--mode print`) |
| `--mode` | | `interactive` (default), `print`, `json`, `rpc` |
| `--provider` | | Provider to use, e.g. `anthropic`, `openai`, `groq` |
| `--model` | | Model ID, or `provider/model` shorthand |
| `--resume [ID]` | `-r` | Resume the most recent or a specified session |
| `--fork ID` | | Fork a specified session at startup |
| `--ephemeral` | `-e` | Don't save this session to disk |
| `--theme` | `-t` | UI theme: `dark`, `light`, or a custom theme |
| `--cwd PATH` | `-c` | Set the working directory |
| `--output-format` | `-f` | Non-interactive output: `text` or `json` |
| `--quiet` | `-q` | Hide the non-interactive spinner |
| `--version` | `-v` | Print the installed version |
| `--help` | `-h` | Show help message |

Full flag list, environment variables, and exit codes: [CLI reference](docs/cli-reference.md).

### Subcommands

```bash
tau auth        # Manage provider credentials (login/logout, list)
tau install      # Install a package (extension/skill/theme)
tau remove        # Remove an installed package
tau list           # List installed packages
tau update          # Update installed packages
```

### Interactive slash commands

Type these inside an interactive session (`tau`):

| Command | What it does |
|---|---|
| `/new` | Start a fresh session |
| `/resume` | Browse and resume a past session |
| `/fork [entry-id]` | Branch the session tree at a specific entry |
| `/tree` | Navigate the session tree, switch branches |
| `/clone` | Duplicate the current session at the current position |
| `/compact` | Summarize and compact the current context |
| `/session` | Show session info, message counts, and stats |
| `/model` | Pick a model by modality |
| `/theme` | Open the theme picker |
| `/effort` | Set the thinking effort level |
| `/login` | Save credentials for a provider (API key or OAuth) |
| `/logout` | Remove stored credentials for a provider |
| `/clear` | Clear all messages from the current session |
| `/copy` | Copy the last assistant message to the clipboard |
| `/reload` | Reload extensions, skills, prompts, and settings |
| `/settings` | Show current settings |
| `/extensions` | Enable or disable extensions by scope |
| `/watch <url> [question]` | Load public video metadata/captions via `yt-dlp` |
| `/help` or `/?` | List all commands and keyboard shortcuts |
| `/quit`, `/q`, or `/exit` | Exit Tau |

Full interactive workflow guide: [Usage](docs/usage.md).

## Compared to Pi

| Area | Pi | Tau |
|---|---|---|
| Language | TypeScript | Python |
| TUI rendering | Line-level diffing — rewrites a full line if any part of it changed | Cell-level diffing (`Buffer`/`Cell`, modeled after [ratatui](https://github.com/ratatui/ratatui)'s `Buffer::diff`) — only the changed cells within a row are redrawn |
| LLM providers | ~40, including many CN/regional and gateway vendors | 14 major providers |
| Audio (TTS/STT) | Not supported | ElevenLabs, Sarvam, Gemini, OpenAI |
| Image/video generation | Not supported | OpenAI, Gemini, OpenRouter, Fal, Zai |
| Sandboxing | microVM sandbox (Gondolin) is an example extension, excluded from the main build — the user wires it in themselves | `microsandbox` microVM ships as a builtin extension, enabled by default |
| Packaging | 5 separately published npm packages | Single PyPI package |

Core mechanics — built-in tools, session branching/compaction, extension and
hook API, and the interactive/print/RPC execution modes — are functionally
equivalent between the two.

## Referencing files

Type `@` in the interactive editor to search for a project file:

```text
Review @src/service.py and add tests for its error handling.
```

For one-shot execution, attach a file explicitly:

```bash
tau -p "Explain this file" @src/service.py
```

Tau also discovers project instructions from `AGENTS.md` and `CLAUDE.md`.
See [Project Context Files](docs/project-context.md) for trust and discovery
behavior.

## Authentication and configuration

Tau resolves provider credentials in this order:

1. A programmatic runtime override
2. A credential saved in `~/.tau/auth.json` (including keys saved by `/login`)
3. A provider environment variable such as `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, and `GOOGLE_API_KEY`

Settings are merged in this order:

1. Built-in defaults
2. `~/.tau/settings.json`
3. `.tau/settings.json`
4. Environment variables
5. Command-line options

See [Authentication](docs/auth.md), [Installation](docs/installation.md), and
[Inference Providers](docs/inference-providers.md) for provider-specific
setup.

## Documentation

- [Quickstart](docs/quickstart.md) — First session in five minutes
- [Usage](docs/usage.md) — Interactive workflows and commands
- [CLI Reference](docs/cli-reference.md) — Command-line options and modes
- [Inference Providers](docs/inference-providers.md) — Providers and speech timestamps
- [Sessions](docs/sessions.md) — Persistence, branching, and compaction
- [Tools](docs/tools.md) — Built-in and custom tools
- [Extensions](docs/extensions.md) — Tools, commands, hooks, and plugins
- [Terminal UI](docs/tui.md) — Rendering, Markdown, math, and components
- [Python API](docs/python-api.md) — Embed Tau in another application
- [Architecture](docs/architecture.md) — Internal design and data flow

The complete documentation index is available at [docs/index.md](docs/index.md).

## Install from source

```bash
git clone https://github.com/Jeomon/Tau.git
cd Tau
pip install -e .
tau
```

## Security

Tau executes enabled tools with the operating-system permissions of the process
that launched it. The built-in `sandbox` extension routes terminal execution
through a `microsandbox` microVM by default, but requires the `microsandbox`
package and a supported platform — otherwise it falls back to unsandboxed host
execution. Review project instructions and commands before approving work in
untrusted repositories, and verify the sandbox is actually active (`/sandbox`)
when stronger isolation matters.

Dependency versions are pinned and recorded in `uv.lock`. See
[SECURITY.md](SECURITY.md) for vulnerability reporting and supply-chain
practices.

## Development

```bash
mypy tau/
pyright tau/
ruff check tau/
ruff format tau/
python -m pytest
```

See [Development Setup](docs/development.md) and
[Contributing](CONTRIBUTING.md).

## License

Tau is licensed under the [MIT License](LICENSE).
