# Prompt Templates

Prompt templates are Markdown files that expand into a user message. Type `/name` in the editor, where `name` is the filename without `.md`, and the expanded body is sent to the model.

Use a template when you want to inject a fixed instruction on demand. Use a [skill](skills.md) when the model should decide for itself that a body of instructions applies.

## Table of Contents

- [Locations](#locations)
- [Format](#format)
- [Frontmatter](#frontmatter)
- [Invoking a Template](#invoking-a-template)
- [Argument Substitution](#argument-substitution)
- [Built-In Templates](#built-in-templates)
- [Loading Rules](#loading-rules)
- [Next Steps](#next-steps)

## Locations

Tau loads templates from four sources, each overriding the previous when names collide:

| Precedence | Source | Location |
|------------|--------|----------|
| Lowest | Built-in | `tau/builtins/prompts/` |
| | Global | `~/.tau/prompts/*.md` |
| | Project | `.tau/prompts/*.md` relative to cwd |
| Highest | Packages and settings | Paths contributed by installed packages and the `prompts` settings array |

Template names are compared case-insensitively, so a project `review.md` replaces the built-in `review` template.

Add extra directories in `settings.json`:

```json
{
  "prompts": ["~/team-prompts"]
}
```

Project templates load only after the project is trusted. See [Project Context Files](project-context.md#trust-and-security).

## Format

A template is frontmatter plus a body. The filename determines the command name: `explain.md` becomes `/explain`.

```markdown
---
description: Explain code or a concept clearly
argument-hint: <file or topic>
---
Explain the following clearly and concisely. Use plain language and give a concrete example if it helps.

$@
```

## Frontmatter

| Field | Required | Description |
|-------|----------|-------------|
| `description` | No | One-line description shown in the `/` autocomplete. Falls back to the body's first non-empty line, stripped of leading `#` and truncated to 120 characters |
| `argument-hint` | No | Expected arguments, shown before the description in autocomplete. `argument_hint` is accepted as an alias |

Use `<angle brackets>` for required arguments and `[square brackets]` for optional ones:

```markdown
---
description: Review code or changes for issues
argument-hint: <files or description>
---
```

> **Note:** Frontmatter is parsed line by line, not as full YAML. Keep each field on a single line; nested structures and lists are not supported.

A template with an empty body fails to load and is reported as a load error.

## Invoking a Template

Type `/` followed by the template name. Autocomplete lists available templates with their hints and descriptions.

```bash
/explain                          # No arguments
/explain src/auth.py              # One argument
/review src/auth.py "focus on session handling"   # Quoted multi-word argument
```

Arguments are split shell-style with `shlex`, so quotes group words into a single argument. If the argument string has unbalanced quotes, Tau falls back to splitting on whitespace rather than failing.

If a name matches a built-in command, the command wins, and templates are only consulted when no command matches.

## Argument Substitution

| Pattern | Meaning |
|---------|---------|
| `$1` … `$9` | Positional argument, 1-based |
| `${1}` … `${N}` | Same, brace form; supports indices above 9 |
| `$@` or `$ARGUMENTS` | All arguments joined with spaces |
| `${1:-default}` | Positional argument, or `default` when absent |
| `${@:N}` | Arguments from index N onward, joined |
| `${@:N:L}` | `L` arguments starting at index N, joined |

A referenced argument that was not supplied expands to an empty string. Braced forms are substituted first, then `$@`/`$ARGUMENTS`, then bare `$1`–`$9`.

> **Note:** The bare `$1`–`$9` form only covers single digits. For a tenth argument or beyond, use the brace form `${10}`. There is no `${@:-default}` form; defaults are only available for positional arguments.

### Examples

Positional argument with a hint:

```markdown
---
description: Explain a symbol from the codebase
argument-hint: <symbol-name>
---
Find and explain `$1` in this codebase. Include where it's defined, what it does, and where it's used.
```

`/explain-symbol PromptBuilder` expands `$1` to `PromptBuilder`.

Fixed first argument plus a remainder:

```markdown
---
description: Translate text to a target language
argument-hint: <language> <text...>
---
Translate the following to ${1}:

${@:2}
```

`/translate French "good morning" everyone` sets `${1}` to `French` and `${@:2}` to `good morning everyone`.

Optional argument with a default:

```markdown
---
description: Run a code review with an optional focus area
argument-hint: [focus-area]
---
Review the most recent changes.
${1:-Check for correctness, style, and security.}
```

`/quick-review` uses the default sentence; `/quick-review "error handling"` replaces it.

## Built-In Templates

Tau ships seven templates. Override any of them by creating a file with the same name in `~/.tau/prompts/` or `.tau/prompts/`.

| Command | Argument Hint | Description |
|---------|---------------|-------------|
| `/commit` | `[context]` | Write a commit message for staged changes |
| `/docs` | `<file or function>` | Write or improve documentation |
| `/explain` | `<file or topic>` | Explain code or a concept clearly |
| `/fix` | `<error or description>` | Fix a bug or error |
| `/refactor` | `<file or description>` | Refactor code for clarity or performance |
| `/review` | `<files or description>` | Review code or changes for issues |
| `/test` | `<file or function>` | Write tests for the given code |

The bundled `commit.md` is a complete worked example of the default pattern:

````markdown
---
description: Write a commit message for staged changes
argument-hint: [context]
---
Write a concise git commit message for the staged changes. Follow conventional commit style (type: short summary). If context is provided, use it to inform the message.

${1:-no additional context}
````

Running `/commit "part of the auth refactor"` sends the body with `${1:-…}` replaced by `part of the auth refactor`. Running bare `/commit` substitutes `no additional context`.

## Loading Rules

- Discovery is **non-recursive**: only `*.md` files directly inside a prompts directory are loaded. Subdirectories are ignored.
- The template name is the filename stem, lowercased.
- Files are loaded in sorted order within each directory.
- Templates appear in the `/` command palette. There is no separate `/prompts` command.

## Next Steps

- [Skills](skills.md): Instruction sets the model loads on its own
- [Extensions](extensions.md): Register templates and commands programmatically
- [Settings](settings.md): The `prompts` path array and other configuration
- [Usage Guide](usage.md): Interactive mode commands
