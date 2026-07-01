# Project Context Files

Tau automatically discovers and includes project-specific instructions from `AGENTS.md` or `CLAUDE.md` in the agent's system prompt. This lets you provide project-level guidance without modifying tool configuration or global settings.

## Overview

When you run Tau in a project directory, it:

1. Looks for `AGENTS.md` or `CLAUDE.md` (case-insensitive)
2. Walks from the Git repository root through the current directory
3. If the project is trusted, includes one context file from each directory
4. Prioritizes the closest file above parent files, general Tau rules, and tool guidelines

Outside a Git repository, Tau checks only the current directory.

This is useful for:
- **Coding standards** — Define how the agent should write code
- **Project conventions** — Specify naming patterns, directory structure expectations
- **Tools & workflows** — Document project-specific development workflows
- **Context** — Provide background on architecture, constraints, decisions

## Usage

### Creating a project context file

Create an `AGENTS.md` file in your project root:

```markdown
# Project Guidelines

## Code Style
- Use type hints for all functions
- Follow PEP 8 naming conventions
- Keep functions under 50 lines

## File Organization
```
src/
├── models/       # Database models
├── handlers/     # Request handlers
├── utils/        # Utility functions
└── tests/        # Test files
```

## Architecture Notes
- We use FastAPI for the REST API
- Pydantic for data validation
- SQLAlchemy for database access

## Common Tasks
- Run tests: `pytest tests/`
- Format code: `black src/`
- Type check: `mypy src/`
```

Or use `CLAUDE.md` for the same purpose (they're equivalent).

### File priority

Tau checks for these names case-insensitively, in this order:

1. `AGENTS.md`
2. `CLAUDE.md`

The first one found in each directory is used. When multiple directories provide
instructions, Tau orders them from the repository root to the current directory.
The closest file takes precedence if instructions conflict.

## Trust & Security

Project context files are only loaded if:
1. **Not disabled** via `--no-context-files` flag
2. **Project is trusted** (via CLI flags, trust store, or settings policy)

### Trust decisions

By default, Tau's behavior is "ask" — it will ask if the project should be trusted the first time you run it (if there are trust decisions to make).

**Control trust behavior with:**

#### CLI Flags (one-time)

**Always trust this project for this run:**
```bash
tau --approve
```

**Never trust this project for this run:**
```bash
tau --no-approve
```

**Disable project context for this run:**
```bash
tau --no-context-files
```

#### Settings (persistent)

Configure the default project trust policy in `~/.tau/settings.json`:

```json
{
  "project_trust": "ask"
}
```

**Options:**
- `"ask"` (default) — Ask each time (stored in `~/.tau/trust.json`)
- `"always"` — Always trust all projects
- `"never"` — Never trust any project

Example configurations:

```json
{
  "project_trust": "always"
}
```

For teams using Tau, you can:
- Set global policy to `"ask"` — Users decide per project
- Set global policy to `"always"` — Auto-load all context files
- Set global policy to `"never"` — Require explicit `--approve` flag

## System Prompt Integration

Project context is included in the system prompt in the "# Project Instructions" section:

```
You are a coding agent...

## How to work
- Always use absolute paths...

# Available Tools
- **read** — Read files...

Tau documentation
- README: ...

# Project Instructions

Project-specific guidelines and rules (from AGENTS.md):

[Content of AGENTS.md here]

Files are ordered from the repository root toward the current directory; later
files take precedence.
```

The agent sees this and prioritizes project instructions when making decisions.

## Examples

### Example 1: Python project

**AGENTS.md:**
```markdown
# Python Project Rules

## Style
- Type annotations required
- Use f-strings for formatting
- No wildcard imports
- Docstrings for all public functions

## Testing
- Run `pytest` before committing
- Keep test coverage above 80%
- Use fixtures for common setup

## Code Review
- Explain the "why" in docstrings
- Use descriptive variable names
- Keep functions focused and small
```

### Example 2: JavaScript/TypeScript project

**CLAUDE.md:**
```markdown
# TypeScript Conventions

## Setup
- Node.js 18+
- Pnpm for package management
- ESLint + Prettier for formatting

## Code Rules
- Strict TypeScript mode enabled
- No `any` types without justification
- Async/await preferred over promises
- Use named exports, not default

## Build & Test
- Build: `pnpm run build`
- Test: `pnpm test`
- Lint: `pnpm run lint`
```

### Example 3: Monorepo

**AGENTS.md:**
```markdown
# Monorepo Structure

## Packages
- `packages/core/` — Core library
- `packages/cli/` — CLI interface
- `packages/web/` — Web UI
- `packages/docs/` — Documentation

## Development
- Each package has its own test suite
- Root `test` script runs all tests
- Use `npm` workspaces

## Deployment
- Build: `npm run build` from root
- Core: Publish to npm
- Web: Deploy to Vercel
- CLI: GitHub releases
```

## Common Issues

**Project context not appearing**

- Check the file exists between the repository root and current directory
- Verify the filename is `AGENTS.md` or `CLAUDE.md`, in any letter case
- Check project trust: `tau --approve` or `/trust` command
- Use `--no-context-files` to confirm it's being loaded

**Want to see if it's loaded**

- Inspect the effective system prompt through the Python API or an extension context

**Trust decisions not persisting**
- By default, trust decisions are stored in `~/.tau/trust.json`
- Use `--approve` to explicitly trust for that run
- Check `~/.tau/trust.json` to see stored decisions

## Comparison with other approaches

| Approach | When to use | Pros | Cons |
|----------|-----------|------|------|
| **AGENTS.md/CLAUDE.md** | Team project guidelines | Lightweight, auto-discovered, version-controlled | Limited to per-project |
| **Custom prompt (`--system`)** | Complete one-off prompt replacement | Flexible | Bypasses generated tools, project context, skills, Git, environment, and append sections |
| **settings.json** | User/global preferences | Persistent | Not project-specific |
| **Environment variables** | Sensitive data, deployment-specific | Secure, flexible | Not human-readable |

For team projects, **AGENTS.md/CLAUDE.md is recommended** because:
- Stays with the codebase (Git history)
- Clear intent and discoverability
- Auto-loaded without configuration
- Can be reviewed in PRs

## See Also

- [Usage Guide](usage.md) — Interactive commands and modes
- [Settings](settings.md) — Configuration and preferences
- [Extensions](extensions.md) — Custom tools and commands
