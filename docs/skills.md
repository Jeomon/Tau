# Skills

Skills are reusable instruction sets that the model can load on demand. They let you encode project-specific workflows, conventions, or multi-step procedures in plain Markdown files — without writing any Python.

## How Skills Work

When a session starts, tau scans the skill directories and injects an `<available_skills>` block into the system prompt listing each skill's name, description, and file path. When a user's request matches a skill's description, the model reads the skill file and follows its instructions.

Skills are loaded lazily — the model reads the file only when it decides the skill is relevant, which keeps the system prompt short regardless of how many skills you have.

## File Locations

Skills are loaded from three sources:

| Priority | Location | Scope |
|----------|----------|-------|
| Highest | `.tau/skills/` in the project root | Project-only |
| | `~/.tau/skills/` | Global (all projects) |
| Lowest | `tau/builtins/skills/` | Built-in skills |

Higher-priority skills override lower-priority ones with the same name.

## Built-in Skills

Tau includes these skills:

| Skill | Purpose |
|-------|---------|
| `code-review` | Review code for correctness, security, and maintainability |
| `debug` | Diagnose failures systematically and verify fixes |
| `git-commit` | Prepare focused commits with conventional messages |
| `skill-creator` | Create or update Tau skills and reusable resources |

## Creating a Skill

### Single-file skill

A `.md` file at the root of a skills directory becomes a skill. The filename (minus extension) is the skill name:

```text
~/.tau/skills/
    refactor.md          # skill name: "refactor"
    write-tests.md       # skill name: "write-tests"
```

### Directory skill

A subdirectory containing `SKILL.md` is loaded as a single skill. The directory name becomes the skill name:

```text
~/.tau/skills/
    deploy/
        SKILL.md         # skill name: "deploy"
        helpers.sh       # referenced from SKILL.md
        checklist.md
```

Use this layout when your skill needs to reference other files (scripts, templates, checklists).

## Skill File Format

Skills use a YAML frontmatter block at the top:

```markdown
---
name: refactor
description: Refactor Python code for clarity, following project conventions
---

## Refactoring Guidelines

1. Prefer small, focused functions (< 30 lines).
2. Use dataclasses instead of plain dicts for structured data.
3. Add type annotations to all public functions.
4. Check `docs/style-guide.md` for project-specific conventions.

## Steps

1. Read the target file first to understand context.
2. Identify the refactoring opportunities.
3. Apply changes incrementally, explaining each decision.
4. Run tests after: `pytest -x`
```

### Frontmatter fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No | Skill name (defaults to filename stem or directory name) |
| `description` | **Yes** | One-line description shown to the model — this determines when the skill is loaded |
| `disable-model-invocation` | No | Set to `true` to hide from the model (skill still loads but isn't listed in the prompt) |

The `description` field is critical — write it so the model can match it against user intent:

```markdown
---
description: Deploy the application to staging or production environments
---
```

## Invoking a Skill Explicitly

You can invoke a skill directly by name, bypassing the model's matching:

```text
/skill:refactor
/skill:deploy staging
```

Arguments after the skill name are available inside the skill as `$1`, `$2`, `$@` — the same substitution syntax as [Prompt Templates](prompts.md#argument-substitution).

## Skill Discovery

Invokable skills appear in the `/` command palette as `/skill:<name>`. There
is no separate `/skills` command.

## Disabling a Skill

To hide a skill from the model while keeping it available for `/skill:` invocation:

```markdown
---
name: internal-notes
description: Internal notes not meant for the model
disable-model-invocation: true
---
```

## Example Skills

### Code review

```markdown
---
description: Review code for bugs, style issues, and security vulnerabilities
---

Review the code at $1 (or the files mentioned in the conversation if no argument).

Check for:
- Logic errors and off-by-one bugs
- Unhandled edge cases and error conditions
- Security issues: injection, path traversal, credential exposure
- Style: naming, function length, unnecessary complexity

For each issue, cite the exact line and suggest a fix.
```

### Commit helper

```markdown
---
description: Write a conventional commit message for the current staged changes
---

1. Run `git diff --staged` to see what's changing.
2. Summarise in a conventional commit format: `type(scope): short description`
3. Types: feat, fix, refactor, docs, test, chore
4. Keep the subject under 72 characters.
5. Add a body if the change needs explanation.
6. Output only the commit message — no commentary.
```

## Next Steps

- [Prompts](prompts.md) — Reusable prompt templates with argument substitution
- [Extensions](extensions.md) — Register skills programmatically from Python
- [Settings](settings.md) — Session configuration
