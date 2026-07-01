# Prompt Templates

Prompt templates are reusable Markdown files that expand into user messages. They let you define frequently-used instructions once and invoke them with a short slash command, optionally passing arguments.

## File Locations

Templates are loaded from three sources:

| Priority | Location | Scope |
|----------|----------|-------|
| Highest | `.tau/prompts/` in the project root | Project-only |
| | `~/.tau/prompts/` | Global (all projects) |
| Lowest | `tau/builtins/prompts/` | Built-in templates |

Higher-priority templates override lower-priority ones with the same name.

## Creating a Template

Save a `.md` file in a prompts directory. The filename (minus extension) becomes the template name:

```text
~/.tau/prompts/
    explain.md       # invoked as /explain
    review.md        # invoked as /review
    summarise.md     # invoked as /summarise
```

### Template format

```markdown
---
description: Explain this code in plain English
argument-hint: [symbol or file path]
---

Explain the following code clearly and concisely. Assume the reader knows
programming but is unfamiliar with this codebase:

$@
```

### Frontmatter fields

| Field | Required | Description |
|-------|----------|-------------|
| `description` | Recommended | One-line description shown in the template picker |
| `argument-hint` | No | Hint shown in the UI for what arguments to pass, e.g. `[file path]` |

If `description` is omitted, tau uses the first non-empty line of the template body (up to 120 characters).

## Invoking a Template

```text
/explain path/to/file.py
/review src/auth.py focus on security
/summarise
```

The template body is expanded with your arguments and sent as a user message.

## Argument Substitution

Templates support shell-style argument placeholders:

| Pattern | Meaning |
|---------|---------|
| `$1`, `$2`, … | Positional argument (1-based) |
| `$@` or `$ARGUMENTS` | All arguments joined with spaces |
| `${1:-default}` | Positional argument with fallback default |
| `${@:N}` | Arguments from index N onwards (1-based) |
| `${@:N:L}` | Arguments from index N, length L |

### Examples

```markdown
---
description: Explain a symbol from the codebase
argument-hint: <symbol-name>
---

Find and explain `$1` in this codebase. Include:
- Where it's defined
- What it does
- Where it's used
```

```markdown
---
description: Translate text to a target language
argument-hint: <language> <text...>
---

Translate the following to ${1}:

${@:2}
```

```markdown
---
description: Run a code review with optional focus area
argument-hint: [focus-area]
---

Review the most recent changes.
${1:-Check for correctness, style, and security.}
```

## Listing Templates

Loaded templates appear in the `/` command palette. There is no separate
`/prompts` command.

## Built-in Templates

Tau ships with a small set of built-in templates. They serve as starting points — copy and modify them in `~/.tau/prompts/` to override.

## Example Templates

### Code explainer

```markdown
---
description: Explain code in plain English, suitable for onboarding
argument-hint: [file or symbol]
---

Explain $@ clearly:
- What problem it solves
- How it works at a high level
- Any non-obvious design decisions
- Gotchas a new contributor should know
```

### PR description generator

```markdown
---
description: Generate a pull request description from recent changes
---

1. Run `git log main..HEAD --oneline` to see commits.
2. Run `git diff main..HEAD --stat` for a file summary.
3. Write a PR description with:
   - A one-line summary (under 72 chars)
   - A "What changed" section with bullet points
   - A "Why" section explaining the motivation
   - A "How to test" checklist
```

### Debug helper

```markdown
---
description: Help debug an error or unexpected behaviour
argument-hint: <error message or description>
---

I'm seeing this issue:

$@

Help me debug it:
1. Identify the most likely causes
2. Suggest diagnostic steps
3. Propose a fix once the cause is confirmed
```

## Next Steps

- [Skills](skills.md) — Longer instruction sets the model loads automatically
- [Extensions](extensions.md) — Register prompt templates programmatically
- [Usage Guide](usage.md) — Interactive mode commands
