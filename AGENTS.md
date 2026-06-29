# Development Rules for Tau

## What is Tau?

**Tau** is a Python CLI framework for building interactive agent applications. It provides:

- **Terminal UI**: Multi-line editor, message history, inline pickers, markdown rendering
- **Multi-provider**: Anthropic Claude, OpenAI GPT, Google Gemini, Mistral, Ollama, Azure OpenAI
- **Session management**: Persistent JSONL-based sessions with branching, forking, resumption
- **Tool execution**: Built-in tools (terminal, read, write, edit, glob, grep, ls) + extensible custom tools
- **Plugin system**: Custom tools, slash commands (`/model`, `/theme`, etc.), hooks, themes, skills, prompts
- **Auto-compaction**: Summarizes old messages when approaching context limits
- **Python API**: Embed Tau as a library in Python applications
- **JSON-RPC**: IDE integration via bidirectional JSON-RPC protocol

**Installation**: `pip install -e .` then run `tau`

**Configuration**: Global `~/.tau/settings.json`, project-local `.tau/settings.json`

**Sessions**: Auto-saved to `~/.tau/sessions/` (JSONL format)

## Where are the docs?

All documentation is in `docs/`. The canonical navigation is defined in `docs/docs.json`.

| Doc | What it covers |
|-----|---------------|
| `docs/quickstart.md` | Five-minute getting started guide |
| `docs/installation.md` | Provider setup and auth |
| `docs/architecture.md` | System design, module overview, data flow |
| `docs/project-structure.md` | Detailed module breakdown |
| `docs/usage.md` | Interactive mode, slash commands, context files |
| `docs/cli-reference.md` | All CLI flags |
| `docs/inference-providers.md` | Supported LLMs and authentication |
| `docs/messages.md` | Message types and context lifecycle |
| `docs/sessions.md` | Session tree, branching, compaction, JSONL format |
| `docs/tools.md` | Built-in and custom tools, ToolRegistry |
| `docs/settings.md` | All settings fields, compaction config, retry |
| `docs/extensions.md` | Writing extensions, hooks, events, hot-reload |
| `docs/themes.md` | YAML theme format, built-in themes |
| `docs/skills.md` | Skill files, frontmatter, invocation |
| `docs/prompts.md` | Prompt templates, argument substitution |
| `docs/keybindings.md` | Keyboard shortcuts and customization |
| `docs/auth.md` | Credential resolution order, auth file format |
| `docs/python-api.md` | Programmatic usage, `RuntimeConfig`, `Runtime` |

## Documentation Consistency Rule

**When the codebase changes in a meaningful way, update the docs in the same commit.**

Meaningful changes that require a doc update:

- A setting is added, renamed, or removed — update `docs/settings.md`
- A new message type or session entry type is added — update `docs/sessions.md` and `docs/messages.md`
- A tool source label changes (e.g. `"config"` → `"runtime"`) — update `docs/tools.md` and `docs/python-api.md`
- A new extension hook or event is added — update `docs/extensions.md`
- A new built-in theme is added or renamed — update `docs/themes.md`
- A new slash command is added — update `docs/usage.md` and `docs/cli-reference.md`
- The Python API surface changes (`RuntimeConfig`, `Runtime`, registry) — update `docs/python-api.md`
- A new top-level doc is added — add it to `docs/docs.json` navigation and link from `docs/index.md`
- The module structure changes — update `docs/project-structure.md` and the module table below

If you are unsure whether a change is doc-worthy, err on the side of updating. A stale doc is a bug.

## Conversational Style

- Keep answers short and concise
- No emojis in code, commits, or comments
- No fluff or cheerful filler text
- Technical prose only, be direct
- When the user asks a question, answer it first before making edits

## Code Quality

- Use type hints in all code
- Follow PEP 8 style guide
- Python 3.13+ features are preferred
- Keep functions focused and testable
- Add docstrings for public APIs

## Commands

After code changes, run:

```bash
mypy tau/              # Type checking
pyright tau/           # Alternative type checker
ruff check tau/        # Linting
ruff format tau/       # Code formatting
python -m pytest      # Tests
```

Fix all errors and warnings before committing.

## Testing

- Write tests for new features
- Run tests locally before committing: `python -m pytest`
- Place tests in `tests/` with matching module names
- Use pytest fixtures for common setup

## Git

Committing:

- Only commit files YOU changed in THIS session
- Stage explicit paths: `git add path1.py path2.py`
- Never use `git add -A` or `git add .`
- Message format: `{feat,fix,docs,refactor}: <description>`
  - `feat`: New feature
  - `fix`: Bug fix
  - `docs`: Documentation only
  - `refactor`: Code restructuring (no behavior change)

Never run (these destroy work or bypass safety):

- `git reset --hard`
- `git checkout .`
- `git clean -fd`
- `git commit --no-verify`

## Module Organization

The codebase is organized by function, not by feature:

| Module | Purpose |
|--------|---------|
| `tau.agent` | Agent execution logic and phase state machine |
| `tau.inference` | LLM provider abstraction (Anthropic, OpenAI, Gemini, Mistral, Ollama, Azure) |
| `tau.engine` | Tool execution engine |
| `tau.tui` | Terminal UI — renderer, input editor, theme, keybindings |
| `tau.session` | Session JSONL storage, context building, compaction, branch summarization |
| `tau.settings` | Configuration loading and merging |
| `tau.auth` | Credential storage and resolution |
| `tau.extensions` | Plugin system — loading, API, runtime context |
| `tau.hooks` | Event system |
| `tau.tool` | Tool types and ToolRegistry |
| `tau.skills` | Skill file loading and injection |
| `tau.prompts` | Prompt template loading and argument substitution |
| `tau.resources` | Unified discovery of extensions, skills, prompts, and themes |
| `tau.themes` | Theme loading (YAML/JSON) and built-in themes |
| `tau.builtins` | Built-in tools, commands, skills, themes |
| `tau.runtime` | Agent runtime — wires agent, session, engine, extensions together |
| `tau.console` | CLI entry point |
| `tau.message` | Message type definitions (`AgentMessage` union) |

See `docs/project-structure.md` for the full breakdown.

## Built-in Commands

| Command | Description |
|---------|-------------|
| `/new` | Start a fresh session |
| `/resume` | Browse and resume a past session interactively |
| `/fork [entry-id]` | Branch the session tree at a specific entry |
| `/tree` | Navigate the session tree and switch branch |
| `/clone` | Duplicate the current session at the current position |
| `/compact` | Summarise and compact the current context |
| `/name [name]` | Set or show the session display name |
| `/session` | Show session info and message counts |
| `/copy` | Copy the last assistant message to clipboard |
| `/clear` | Clear all messages from the current session |
| `/model` | Open the model picker |
| `/theme` | Open the theme picker |
| `/effort` | Set the thinking effort level |
| `/login` | Save credentials for a provider (API key or OAuth) |
| `/logout` | Remove stored credentials for a provider |
| `/reload` | Reload extensions, skills, prompts, and settings |
| `/settings` | Show current settings |
| `/help` / `/?` | List all commands and shortcuts |
| `/quit` / `/q` / `/exit` | Exit tau |

## Extension Points

Users can extend tau through:

- **Tools**: Custom tools the agent can call
- **Commands**: Slash commands (`/command`)
- **Hooks**: React to events (session_start, tool_execute, before_compaction, etc.)
- **Themes**: Custom terminal color schemes (YAML files in `~/.tau/themes/`)
- **Skills**: Instruction sets the model loads automatically (Markdown in `~/.tau/skills/`)
- **Prompts**: Reusable prompt templates with argument substitution (`~/.tau/prompts/`)

See `docs/extensions.md` for implementation details.

## Inference Providers

Tau supports multiple LLM providers:

- Anthropic (Claude)
- OpenAI (GPT)
- Google Gemini
- Mistral AI
- Ollama (local)
- Azure OpenAI

All providers are abstracted behind `tau.inference.InferenceClient`. See `docs/inference-providers.md` for setup.

## Sessions

Sessions are persisted to disk as JSONL files and can be resumed, forked, or cloned. The session model is a tree — navigating away from a branch triggers branch summarization. See `docs/sessions.md` for details. Session files live in `~/.tau/sessions/`.

## Configuration

Configuration uses JSON files with inheritance:

1. Built-in defaults
2. `~/.tau/settings.json` (global)
3. `.tau/settings.json` (project)
4. Environment variables
5. Command-line flags

Settings files are JSON only (not YAML) — they are both read and written by tau at runtime. See `docs/settings.md` for all available options.

## Themes

Themes are YAML files (`.yaml` / `.yml`) stored in `~/.tau/themes/` or `.tau/themes/`. Tau includes 16 built-in themes: `default`, `dracula`, `nord`, `gruvbox`, `catppuccin`, `ayu-dark`, `everforest`, `horizon`, `kanagawa`, `material-ocean`, `monokai`, `night-owl`, `one-dark`, `rose-pine`, `solarized-dark`, `tokyo-night`, plus `dark` and `light` base themes for extending. See `docs/themes.md`.

## Tool Sources

The `ToolRegistry` tags every registered tool with a source string:

| Source | Meaning |
|--------|---------|
| `"builtin"` | From `tau.builtins.tools.TOOLS` — always present |
| `"extension"` | Registered by a loaded extension |
| `"runtime"` | Passed via `RuntimeConfig.tools` at session start |
| `"mcp"` | Reserved for future MCP server tools |

`replace_source()` lets `/reload` atomically swap all extension tools without touching other sources.

## User Override

If the user's instructions conflict with any rule in this document, ask for explicit confirmation before overriding. Only then execute their instructions.

## Before Contributing

1. Read `docs/quickstart.md` to understand tau
2. Check `docs/architecture.md` to see how components fit together
3. Refer to `docs/` when implementing new features
4. Follow the code style: type hints, docstrings, PEP 8
5. Write tests for new functionality
6. Run the full test suite before committing
7. Update `docs/` to match any meaningful code changes

## Questions?

Refer to the documentation in `docs/`. If documentation is missing or unclear, that's a bug — update the docs.
