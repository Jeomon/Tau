# Project Context Files

Tau discovers `AGENTS.md` and `CLAUDE.md` files in your project and injects them into the system prompt. This gives the agent standing, version-controlled instructions without touching global settings or tool configuration.

## Table of Contents

- [File Names and Precedence](#file-names-and-precedence)
- [Discovery Rules](#discovery-rules)
- [Writing a Context File](#writing-a-context-file)
- [System Prompt Integration](#system-prompt-integration)
- [Trust and Security](#trust-and-security)
- [Choosing an Approach](#choosing-an-approach)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## File Names and Precedence

Tau recognizes exactly two names, matched case-insensitively:

| Order | Name | Notes |
|-------|------|-------|
| 1 | `AGENTS.md` | Checked first |
| 2 | `CLAUDE.md` | Used only when no `AGENTS.md` is present in that directory |

Within a directory, the first match wins, so Tau loads **one** context file per directory, never both. When several case variants exist (`AGENTS.md` and `agents.md`), the fully uppercase spelling is preferred.

## Discovery Rules

1. Tau walks up from the current directory to find the Git repository root.
2. It collects one context file from each directory on the path from that root down to the current directory.
3. Files are ordered **root first**, so the closest file appears last and takes precedence when the model reads top to bottom.
4. Outside a Git repository, only the current directory is checked.

Additional details:

- **Empty files are ignored.** A file whose content is blank after stripping is treated as absent, and `CLAUDE.md` is not consulted as a fallback in that directory.
- **Symlinks are skipped.** Only regular files are considered.
- **Duplicates are removed.** Files resolving to the same inode are loaded once.
- **Unreadable files are skipped**, not fatal.

For a repository laid out like this:

```text
myrepo/                    # Git root
├── AGENTS.md              # Loaded first (lowest precedence)
├── CLAUDE.md              # Ignored — AGENTS.md wins in this directory
└── services/
    └── api/
        ├── AGENTS.md      # Loaded last (highest precedence)
        └── src/
```

Running Tau from `myrepo/services/api/src/` loads `myrepo/AGENTS.md` then `myrepo/services/api/AGENTS.md`. The `services/` directory contributes nothing, since it has no context file.

## Writing a Context File

Create `AGENTS.md` in your project root. There is no required schema: the content is injected as-is, so write whatever the agent should always know.

````markdown
# Project Guidelines

## Code Style
- Type hints on all public functions
- Keep functions under 50 lines
- No wildcard imports

## Layout
```
src/
├── models/       # SQLAlchemy models
├── handlers/     # FastAPI request handlers
└── utils/        # Shared helpers
```

## Common Tasks
- Run tests: `pytest tests/`
- Format: `ruff format src/`
- Type check: `mypy src/`

## Tool Use
- Prefer the `grep` tool over `terminal` with `grep`
- Use `edit` for changes, `write` only for new files
- Never run database migrations
````

Keep it short and imperative. Everything here occupies context on every request, so prefer standing rules over background prose the agent can read on demand; put that in a [skill](skills.md) instead.

## System Prompt Integration

Context files land in a `# Project Instructions` section of the system prompt, after the tool list and before skills. Each file is wrapped in a `<project_instructions>` tag carrying its path, so the model knows which directory each rule set governs:

```xml
# Project Instructions

Project-specific guidelines. Files are ordered from the repository root toward the current directory; later files take precedence:

<project_context>

<project_instructions path="/myrepo/AGENTS.md">
...content...
</project_instructions>

<project_instructions path="/myrepo/services/api/AGENTS.md">
...content...
</project_instructions>

</project_context>
```

The complete prompt is assembled in this order:

| Layer | Source |
|-------|--------|
| Identity | `SYSTEM.md` if present, else the built-in identity |
| Guidelines | General behavior and precedence rules |
| Available Tools | Generated from the active tool list |
| Tau docs | Framework documentation and examples |
| **Project Instructions** | **`AGENTS.md` / `CLAUDE.md`** |
| Skills | The `<available_skills>` block |
| Git snapshot | Branch, redacted remote, status, recent commits |
| Environment | cwd, OS, architecture, shell, date |
| Appended | `APPEND_SYSTEM.md`, verbatim and last |

Passing `--system` or setting `RuntimeConfig.system_prompt` bypasses this builder entirely: tools, project context, skills, Git, and environment sections are all dropped.

## Trust and Security

Project context is injected only when both conditions hold:

1. Context loading is not disabled with `--no-context-files`.
2. The project is trusted.

Trust is a loading guard, not a sandbox. It stops a repository from silently changing Tau's behavior before you approve it; it does not make the repository's contents safe. Instructions inside a context file are prompt input the model will follow, so review them the way you would review code.

### Trust Inputs

Tau asks for a trust decision when the current directory or an ancestor contains any of:

- A project-local `.tau/` directory
- An `.agents/skills/` directory
- An `AGENTS.md` or `CLAUDE.md` anywhere from the Git root down to cwd

### CLI Flags

```bash
tau --approve            # Trust this project for this run (-a)
tau --no-approve         # Do not trust this project for this run
tau --no-context-files   # Load no AGENTS.md / CLAUDE.md at all this run
```

### Persistent Policy

Set the default in `~/.tau/settings.json`:

```json
{
  "project_trust": "ask"
}
```

| Value | Behavior |
|-------|----------|
| `"ask"` | Prompt on first use in a project; the answer is saved (default) |
| `"always"` | Trust every project without asking |
| `"never"` | Never trust project resources |

Saved per-directory decisions live in `~/.tau/trust.json`.

## Choosing an Approach

| Approach | Best for | Trade-off |
|----------|----------|-----------|
| `AGENTS.md` / `CLAUDE.md` | Team-wide project rules | Always in context; costs tokens on every request |
| [Skills](skills.md) | Deep workflows the model loads when relevant | Only the description is always in context |
| [Prompt templates](prompts.md) | Instructions you invoke deliberately | Requires you to type `/name` |
| `.tau/SYSTEM.md` | Replacing the agent's identity for a project | Overrides the default identity layer |
| `.tau/APPEND_SYSTEM.md` | Appending verbatim text last | No structure; easy to bloat |
| `--system` | A one-off, fully custom prompt | Bypasses tools, context, skills, Git, and environment |
| [Settings](settings.md) | User preferences across projects | Not project-specific |

For team projects `AGENTS.md` is the recommended default: it travels with the codebase, is reviewable in pull requests, and needs no configuration.

## Troubleshooting

**Context does not appear in the prompt**

- Confirm the file sits between the Git root and your current directory.
- Confirm the name is `AGENTS.md` or `CLAUDE.md` in any letter case.
- Confirm the file is not empty and is not a symlink.
- Confirm the project is trusted: rerun with `tau --approve`.
- Confirm you are not running with `--no-context-files`.

**Only one file loads in a monorepo**

Tau takes one file per directory. If both `AGENTS.md` and `CLAUDE.md` exist side by side, only `AGENTS.md` is used. Directories with no context file contribute nothing.

**Trust decisions do not persist**

Check `~/.tau/trust.json` for the stored decision. A `project_trust` value of `"never"` in settings blocks project resources regardless of saved decisions.

**Inspecting the effective prompt**

There is no slash command for this. Build the prompt through the Python API or read it from an extension context; see [Python API](python-api.md).

## Next Steps

- [Skills](skills.md): On-demand instruction sets that keep the prompt small
- [Prompt Templates](prompts.md): Slash commands with argument substitution
- [Settings](settings.md): `project_trust` and other configuration
- [Tools](tools.md): What the agent can actually do
