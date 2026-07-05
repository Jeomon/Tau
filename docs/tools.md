# Tools

This page documents the built-in tools available to the agent and how the tool system works.

## Built-In Tools

Tau ships with seven built-in tools covering file I/O, search, and shell execution.

### read

Read a UTF-8 text file. Invalid byte sequences are replaced during decoding.
Every returned line is prefixed with a content-based hashline anchor in the form
`<line>:<hash>|<content>`. Duplicate content, including blank lines, can share a
hash; the line number acts as a proximity hint when `edit` resolves an anchor.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | — | Absolute path to the file to read |
| `offset` | integer | No | `0` | Number of lines to skip; `0` starts at the first line |
| `limit` | integer | No | `2000` | Maximum number of lines to return |

### write

Create a new file or overwrite an existing file entirely.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Absolute path to write |
| `content` | string | Yes | Exact complete UTF-8 text content |

### edit

Replace an inclusive line range using hashline anchors returned by `read`.
The content hash can let an anchor survive line insertions or deletions elsewhere
in the file; when content occurs more than once, its original line number is
used as a proximity hint and the closest occurrence is selected. An empty
`new_content` deletes the selected range. Because the complete file is rewritten,
an edit may normalize line endings. Edit-result diffs also display hashline anchors:
removed lines use their old hashes, while added and context lines use current hashes.
Edit diffs always show every changed line. By default they include three unchanged
lines around each change and collapse larger unchanged gaps into `… (+N lines)`;
Ctrl+O expands those gaps to show the complete file context.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | — | Absolute path to the file |
| `start_anchor` | string | Yes | — | First line to replace, formatted `<line>:<hash>` |
| `end_anchor` | string | Yes | — | Last line to replace; use the start anchor for a single-line edit |
| `new_content` | string | Yes | — | UTF-8 text replacing the range; empty text deletes it |

### terminal

Execute a non-interactive shell command and return the combined stdout + stderr
tail. At most 50 KiB or 2,000 lines are retained in the result. When output is
truncated, the complete output is saved to a temporary file and its path is
included in the result and metadata.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `cmd` | string | Yes | — | Non-interactive shell command to execute |
| `timeout` | integer | No | `30` | Timeout in seconds (max 600) |

Commands run in the agent's current working directory.

Output is streamed through tool-update events while the command runs. Updates
are throttled to at most once every 100 milliseconds, with guaranteed initial
and final updates. The interactive TUI refreshes the existing result block for
each update; when a long result is collapsed, its newest lines remain visible
while the command is running. Timeout and cancellation terminate the command's
complete process tree. Programs may still buffer their own output; use an
unbuffered mode such as `python -u` when immediate output is required.

### glob

Find files matching a glob pattern. Patterns are evaluated relative to `path`.
Ripgrep's default filtering excludes hidden files and files matched by ignore
rules.

Requires `rg` (ripgrep); the tool returns an error when `rg` is unavailable.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | Yes | — | Glob pattern, e.g. `src/**/*.py` |
| `path` | string | No | cwd | Base directory; relative values use Tau's process working directory |

### grep

Search for a regular expression across files. Directory searches are recursive.
Ripgrep's default filtering excludes hidden files and files matched by ignore
rules.

Requires `rg` (ripgrep); the tool returns an error when `rg` is unavailable.

Set `ast: true` to search with [ast-grep](https://ast-grep.github.io) instead,
matching code structurally (via `$VAR`-style meta-variables) rather than by
regex. This is useful for finding a code shape regardless of formatting or
naming, e.g. pattern `$A && $A()`. In this mode `pattern` must be an ast-grep
pattern, not a regex, `case_sensitive` is ignored, and the target language is
inferred per-file from its extension. Requires `ast-grep` (install via the
`tools` optional dependency group); the tool returns an error when it's
unavailable.

Compound statements (`for`/`if`/`while`/`def`/etc.) need their body included
as `$$$BODY` (e.g. `for $ITEM in $LIST:\n    $$$BODY`) — an incomplete
pattern fails to parse and silently returns no matches rather than an error.
If a search unexpectedly finds nothing, run
`ast-grep run --pattern '<pattern>' --lang <lang> --debug-query=pattern`
directly to see how ast-grep parsed the pattern before assuming the code
isn't there.

Metavariable names must be uppercase (`$ARG`, `$MY_VAR`); lowercase,
digit-leading, or kebab-case names are not valid metavariable syntax and are
matched literally. A metavariable must also be the entire content of its AST
node — partial substitution like `obj.on$EVENT` doesn't work.

For queries a single pattern can't express — relational (`has`/`inside`/
`precedes`/`follows`, usually combined with `stopBy: end`), composite
(`all`/`any`/`not`), or matching by node `kind` — pass a `rule` (an ast-grep
YAML rule run via `ast-grep scan --inline-rules`) instead of `pattern`.
`rule` must include a top-level `language` key and takes precedence over
`pattern` when both are set. Example, finding functions that `await` without
a surrounding `try`/`except`:

```yaml
language: python
rule:
  all:
    - kind: function_definition
    - has:
        pattern: await $EXPR
        stopBy: end
    - not:
        has:
            kind: try_statement
            stopBy: end
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | No\* | `""` | Regular expression to search for (an ast-grep pattern when `ast` is true) |
| `path` | string | No | cwd | File or directory; relative values use Tau's process working directory |
| `include` | string | No | `""` | Glob filter for files, e.g. `*.py` (only applies when `path` is a directory) |
| `case_sensitive` | boolean | No | `true` | Whether the pattern is case-sensitive (ignored when `ast` is true) |
| `ast` | boolean | No | `false` | Use ast-grep structural matching instead of ripgrep regex |
| `rule` | string | No | `""` | ast-grep YAML rule for structural queries beyond a single pattern; only used when `ast` is true, and takes precedence over `pattern` |

\* Either `pattern` or (when `ast` is true) `rule` is required.

### ls

List a directory's immediate contents without recursing.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | No | cwd | Directory; relative values use Tau's process working directory |

---

## How Tools Work

1. The agent decides to use a tool and emits a tool call with parameters.
2. The engine validates the parameters against the tool's schema.
3. The tool executes and returns a `ToolResult` containing text content and a `metadata` dict.
4. The result is returned to the model, which decides what to do next.

If a tool call fails (invalid parameters, execution error), the agent receives the error message and can retry or choose a different approach.

---

## Tool Kinds and Execution Modes

Each tool has a `kind` that signals its semantic category and an `execution_mode` that controls scheduling:

**Kinds**: `read`, `edit`, `write`, `execute`, `web`

**Execution modes**:
- `sequential` — an ordering barrier; if any call in a model-produced batch is
  sequential, the complete batch runs one at a time in source order
- `parallel` — runs concurrently only when every call in the batch is parallel
- `batch` — engine-level safe mixed scheduling; equivalent to the default

Tau preserves the model's tool-call order in the resulting tool message.
Parallel completion events may arrive in completion order, but result messages
remain in source order.

For tools using `render_shell="default"`, the TUI owns result collapsing and
the Ctrl+O hint. Thinking and results at or below `tool_result_preview_lines` render in full
without a hint. Tools can set `result_preview_lines` to override the global
threshold or `result_expandable=False` to always show their complete rendered
output. Custom renderers should return their complete semantic output and must
not add expand/collapse hints.

Tool results render as plain text by default. A tool can opt an individual
successful result into Markdown rendering with
`metadata={"_render_format": "markdown"}`. Tau renders the Markdown before
applying the standard preview/collapse shell. Error results remain plain text.

Extensions that enrich model-facing result text can keep the original TUI text
in `_display_content`. They can append structured UI sections through
`_extra_blocks`, where each block contains `lines` and can optionally set its
own `preview_lines` collapse threshold.

---

## Adding Custom Tools

Extensions, Python runtimes, and the standalone engine can register custom
tools. See [Creating Tools](creating-tools.md) for a complete typed example,
registration options, and testing guidance.

The `ToolRegistry` tracks all registered tools by source (`"builtin"`, `"extension"`, `"runtime"`). After `/reload`, extension tools are synced to the live engine immediately without restarting the session.

Terminal output is bounded to a 50 KiB / 2,000-line display tail. Grep and glob
stream bounded ripgrep results and terminate the subprocess when cancelled or
when their result cap is reached. Writes and edits are serialized per resolved
path and committed with an atomic replacement.

---

## Tool Constraints

Add an `AGENTS.md` file to your project to give the agent standing instructions about tool usage:

```markdown
# Project Instructions

- Always run tests after making changes
- Do not run database migrations
- Prefer `grep` over `bash grep` for searching
- Use `edit` for small changes, `write` for new files
```

Tau searches case-insensitively for `AGENTS.md` or `CLAUDE.md` from the Git
repository root through the current directory. See
[Project Context Files](project-context.md) for precedence and trust behavior.

---

## Next Steps

- [Creating Tools](creating-tools.md) - Implement and test custom tools
- [Extensions](extensions.md) - Package tools with other extension features
- [Usage Guide](usage.md) - How to work with the agent
- [Architecture](architecture.md) - How the tool system works internally
