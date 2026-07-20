> Tau can create skills. Run `/skill:skill-creator` and describe your use case.

# Skills

Skills are self-contained instruction packages the model loads on demand. A skill encodes a project-specific workflow, convention, or multi-step procedure in plain Markdown — no Python required.

Use a skill when the model should decide for itself that a body of instructions is relevant. Use a [prompt template](prompts.md) when *you* want to inject a fixed instruction with a slash command.

## Table of Contents

- [Locations](#locations)
- [How Skills Work](#how-skills-work)
- [Skill Commands](#skill-commands)
- [Skill Structure](#skill-structure)
- [Frontmatter](#frontmatter)
- [Validation](#validation)
- [Example](#example)
- [Built-In Skills](#built-in-skills)
- [Next Steps](#next-steps)

## Locations

> **Security:** A skill can instruct the model to take any action, and may ship scripts the model executes. Review skill content before use.

Tau loads skills from four sources, each overriding the previous when names collide:

| Precedence | Source | Location |
|------------|--------|----------|
| Lowest | Built-in | `tau/builtins/skills/` |
| | Global | `~/.tau/skills/` |
| | Project | `.tau/skills/` relative to cwd |
| Highest | Packages and settings | Paths contributed by installed packages and the `skills` settings array |

Skill names are compared case-insensitively and stored lowercased, so a project `deploy` skill replaces a global `Deploy` skill.

To add skill directories from other harnesses, list them in `settings.json`:

```json
{
  "skills": [
    "~/.claude/skills",
    "~/.agents/skills"
  ]
}
```

Project skills load only after the project is trusted. See [Project Context Files](project-context.md#trust-and-security).

## How Skills Work

1. At startup Tau scans every skill location and extracts each skill's name, description, and file path.
2. The system prompt gains an `<available_skills>` block listing those three fields — never the skill body.
3. When a request matches a skill's description, the model calls `read` on the listed location to load the full instructions.
4. The model follows the instructions, resolving relative paths against the skill's own directory.

This is progressive disclosure: descriptions are always in context, bodies load only when needed, so the prompt cost stays flat as you add skills.

The injected block looks like this:

```xml
<available_skills>
  <skill>
    <name>git-commit</name>
    <description>Stage and commit changes with a well-formed commit message</description>
    <location>/path/to/tau/builtins/skills/git-commit/SKILL.md</location>
  </skill>
</available_skills>
```

Models do not always load a skill on their own. Prompt explicitly, or force it with `/skill:name`.

## Skill Commands

Every skill registers a `/skill:<name>` command that loads it immediately, bypassing the model's matching:

```bash
/skill:git-commit                  # Load and follow the skill
/skill:debug the failing auth test # Load with trailing arguments
```

The invoked skill is wrapped for the model as:

```xml
<skill name="git-commit" location="/path/to/SKILL.md">
References are relative to /path/to.

...skill body...
</skill>
```

Arguments after the name are appended verbatim after the closing tag.

> **Note:** Skills do **not** perform argument substitution. `$1`, `$@`, and `${1:-default}` are inert inside a skill body — they are passed through as literal text. Only [prompt templates](prompts.md#argument-substitution) substitute arguments.

Skills appear in the `/` command palette as `/skill:<name>`. There is no `/skills` listing command.

## Skill Structure

A skill is either a single Markdown file or a directory containing `SKILL.md`.

### Single-File Skill

A `.md` file at the **root** of a skills directory becomes a skill named after its filename stem:

```text
~/.tau/skills/
├── refactor.md            # Skill name: "refactor"
└── write-tests.md         # Skill name: "write-tests"
```

Root-level `.md` files are only discovered at the top level of a skills directory, not in subdirectories.

### Directory Skill

A subdirectory containing `SKILL.md` loads as one skill named after the directory. Everything else in the directory is freeform:

```text
~/.tau/skills/
└── deploy/
    ├── SKILL.md           # Required: frontmatter + instructions
    ├── scripts/
    │   └── release.sh     # Helper script the model can run
    ├── references/
    │   └── runbook.md     # Detail loaded on demand
    └── assets/
        └── config.tmpl
```

Use this layout whenever the skill references other files. Tau tells the model that relative paths resolve against the skill's directory, so link them relatively:

```markdown
See [the deploy runbook](references/runbook.md) before proceeding.
```

Tau recurses into subdirectories that do **not** contain `SKILL.md`, so you can group skills into categories:

```text
~/.tau/skills/
└── backend/               # No SKILL.md — just a grouping folder
    ├── migrations/
    │   └── SKILL.md       # Skill name: "migrations"
    └── profiling/
        └── SKILL.md       # Skill name: "profiling"
```

## Frontmatter

Frontmatter is a `---`-delimited block of `key: value` lines at the top of the file. Keys are lowercased; values are read as plain strings.

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No | Skill name, lowercased on load. Defaults to the directory name for `SKILL.md`, or the filename stem for a root `.md` file |
| `description` | **Yes** | What the skill does and when to use it. This is the only text the model sees before loading the skill |
| `disable-model-invocation` | No | `true`, `1`, or `yes` hides the skill from the system prompt. It stays available via `/skill:name` |

Unknown fields are parsed but ignored.

> **Note:** Tau uses a simple line-based parser, not a full YAML parser. Nested structures, multi-line values, and lists are not supported — keep every field on one line. A value containing `:` is preserved, since only the first `:` splits the pair.

### Description Best Practices

The description is the model's only basis for deciding whether to load the skill. Be specific about both capability and trigger.

Good:
```yaml
description: Create or update Tau skills with effective triggering metadata, focused instructions, and optional reusable scripts, references, or assets. Use when a user asks to create, scaffold, revise, validate, or improve a skill.
```

Poor:
```yaml
description: Helps with skills.
```

### Hiding a Skill from the Model

```markdown
---
name: internal-notes
description: Internal release checklist, run manually only
disable-model-invocation: true
---

1. Confirm the changelog is current.
2. Tag the release.
```

This skill never appears in `<available_skills>`, but `/skill:internal-notes` still works.

## Validation

Tau's skill validation is deliberately minimal. A skill fails to load — and is reported as a load error — in exactly these cases:

| Condition | Outcome |
|-----------|---------|
| Missing or empty `description` | Not loaded; error `missing 'description' field` |
| Empty body after frontmatter | Not loaded; error `skill body is empty` |
| File cannot be read | Not loaded; error `read error: <detail>` |

There are no length limits on `name` or `description`, no character-set restrictions on names, and no requirement that `name` match its parent directory. Name collisions do not warn — the higher-precedence location silently wins.

## Example

The bundled `git-commit` skill is a complete minimal skill:

```text
tau/builtins/skills/git-commit/
└── SKILL.md
```

**SKILL.md:**

````markdown
---
name: git-commit
description: Stage and commit changes with a well-formed commit message
---
Stage the relevant files, write a conventional commit message based on the diff, and create the commit. Follow the format `type: short summary` (e.g. `feat:`, `fix:`, `refactor:`). Keep the subject under 72 characters. If there are staged changes already, use them as-is.
````

Invoke it explicitly:

```bash
/skill:git-commit
```

Or simply ask the agent to commit your changes — the description matches, so the model loads the skill on its own.

### A Larger Skill

To create a project skill that references a helper script:

1. Create the directory:

   ```bash
   mkdir -p .tau/skills/deploy/scripts
   ```

2. Write `.tau/skills/deploy/SKILL.md`:

   ````markdown
   ---
   name: deploy
   description: Deploy the service to staging or production. Use when asked to ship, release, or roll back a deployment.
   ---

   # Deploy

   ## Preflight

   Confirm the working tree is clean and tests pass:

   ```bash
   git status --porcelain && pytest -x
   ```

   ## Deploy

   ```bash
   ./scripts/release.sh <staging|production>
   ```

   Consult [the rollback runbook](references/rollback.md) if the health check fails.
   ````

3. Add the helper script and mark it executable:

   ```bash
   chmod +x .tau/skills/deploy/scripts/release.sh
   ```

4. Start Tau in the project and trust it, then confirm the skill is available:

   ```bash
   /skill:deploy staging
   ```

## Built-In Skills

| Skill | Purpose |
|-------|---------|
| `code-review` | Review code changes for bugs, clarity, and correctness |
| `debug` | Diagnose and fix a bug or unexpected behaviour |
| `git-commit` | Stage and commit changes with a well-formed commit message |
| `skill-creator` | Create or update Tau skills, including scripts, references, and assets |

Override any of them by defining a skill with the same name in `~/.tau/skills/` or `.tau/skills/`.

## Next Steps

- [Prompt Templates](prompts.md) — Slash commands with argument substitution
- [Extensions](extensions.md) — Register skills and tools programmatically from Python
- [Project Context Files](project-context.md) — Always-on project instructions and trust
- [Settings](settings.md) — The `skills` path array and other configuration
