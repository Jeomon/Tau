# Tools

This page documents the built-in tools available to the agent and how the tool system works.

## Built-In Tools

Tau ships with seven built-in tools covering file I/O, search, and shell execution.

### read

Read the contents of a file. Every returned line is prefixed with a hashline
anchor in the form `<line>:<hash>|<content>`. The `edit` tool uses these anchors.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | — | Absolute path to the file to read |
| `offset` | integer | No | `0` | Line number to start reading from (0-based) |
| `limit` | integer | No | `2000` | Maximum number of lines to return |

### write

Create a new file or overwrite an existing file entirely.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Absolute path to write |
| `content` | string | Yes | Full file content |

### edit

Replace an inclusive line range using hashline anchors returned by `read`.
The content hash lets an anchor survive line insertions or deletions elsewhere
in the file; when content occurs more than once, its original line number is
used as a proximity hint. Edit-result diffs also display hashline anchors:
removed lines use their old hashes, while added and context lines use current hashes.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | — | Absolute path to the file |
| `start_anchor` | string | Yes | — | First line to replace, formatted `<line>:<hash>` |
| `end_anchor` | string | Yes | — | Last line to replace; use the start anchor for a single-line edit |
| `new_content` | string | Yes | — | Content that replaces the anchored range |

### terminal

Execute a shell command and return combined stdout + stderr.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
`command` | string | Yes | — | Shell command to execute.
`timeout` | integer | No | `30` | Timeout in seconds (max 600)

Commands run in the agent's current working directory.

### glob

Find files matching a glob pattern.

Requires `rg` (ripgrep); the tool returns an error when `rg` is unavailable.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | Yes | — | Glob pattern, e.g. `src/**/*.py` |
| `path` | string | No | cwd | Base directory to search from |

### grep

Search for a regular expression across files.

Requires `rg` (ripgrep); the tool returns an error when `rg` is unavailable.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | Yes | — | Regular expression to search for |
| `path` | string | No | cwd | File or directory to search |
| `include` | string | No | `""` | Glob filter for files, e.g. `*.py` (only applies when `path` is a directory) |
| `case_sensitive` | boolean | No | `true` | Whether the pattern is case-sensitive |

### ls

List the contents of a directory.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | No | cwd | Directory path to list |

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

---

## Adding Custom Tools

Extensions can register new tools. See [Extensions](extensions.md) for how to create a `Tool` subclass and register it via `tau.register_tool(...)` inside a `register(tau)` function.

The `ToolRegistry` tracks all registered tools by source (`"builtin"`, `"extension"`, `"runtime"`). After `/reload`, extension tools are synced to the live engine immediately without restarting the session.

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
