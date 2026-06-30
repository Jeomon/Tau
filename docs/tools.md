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

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | — | Absolute path to the file |
| `start_anchor` | string | Yes | — | First line to replace, formatted `<line>:<hash>` |
| `end_anchor` | string | Yes | — | Last line to replace; use the start anchor for a single-line edit |
| `new_content` | string | Yes | — | UTF-8 text replacing the range; empty text deletes it |

### terminal

Execute a non-interactive shell command and return the combined stdout + stderr
tail. At most 50 KiB or 2,000 lines are retained.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `cmd` | string | Yes | — | Non-interactive shell command to execute |
| `timeout` | integer | No | `30` | Timeout in seconds (max 600) |

Commands run in the agent's current working directory.

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

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | Yes | — | Regular expression to search for |
| `path` | string | No | cwd | File or directory; relative values use Tau's process working directory |
| `include` | string | No | `""` | Glob filter for files, e.g. `*.py` (only applies when `path` is a directory) |
| `case_sensitive` | boolean | No | `true` | Whether the pattern is case-sensitive |

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

---

## Adding Custom Tools

Extensions can register new tools. See [Extensions](extensions.md) for how to create a `Tool` subclass and register it via `tau.register_tool(...)` inside a `register(tau)` function.

The `ToolRegistry` tracks all registered tools by source (`"builtin"`, `"extension"`, `"runtime"`). After `/reload`, extension tools are synced to the live engine immediately without restarting the session.

Terminal output is bounded to a 50 KiB / 2,000-line display tail. Grep and glob
stream bounded ripgrep results and terminate the subprocess when cancelled or
when their result cap is reached. Writes and edits are serialized per resolved
path and committed with an atomic replacement.

---

## Tool Constraints

Add a `.agents.md` file to your project root to give the agent standing instructions about tool usage:

```markdown
# Project Instructions

- Always run tests after making changes
- Do not run database migrations
- Prefer `grep` over `bash grep` for searching
- Use `edit` for small changes, `write` for new files
```

Tau searches for `.agents.md`, `agents.md`, or `~/.tau/agents.md`. The file is automatically loaded at session start and injected into the agent's context.

---

## Next Steps

- [Extensions](extensions.md) - Create custom tools
- [Usage Guide](usage.md) - How to work with the agent
- [Architecture](architecture.md) - How the tool system works internally
