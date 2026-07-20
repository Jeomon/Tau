# Tools

Tools are the typed, schema-validated operations a model can request. This page is the reference for the tools Tau ships with and the execution model the engine applies to them. To build your own, see [Creating Tools](creating-tools.md).

## Table of Contents

- [Built-In Tools](#built-in-tools)
- [Bundled Extension Tools](#bundled-extension-tools)
- [How Tools Work](#how-tools-work)
- [Tool Kinds](#tool-kinds)
- [Execution Modes](#execution-modes)
- [Result Rendering](#result-rendering)
- [The Tool Registry](#the-tool-registry)
- [Constraining Tool Use](#constraining-tool-use)
- [Next Steps](#next-steps)

## Built-In Tools

Tau ships seven built-in tools covering file I/O, search, and shell execution. They are defined in `tau/builtins/tools/` and registered under the `builtin` source.

| Tool | Kind | Execution Mode | Purpose |
|------|------|----------------|---------|
| [`read`](#read) | `read` | `parallel` | Read a UTF-8 text file with hashline anchors |
| [`write`](#write) | `write` | `sequential` | Create or overwrite a file |
| [`edit`](#edit) | `edit` | `sequential` | Replace an anchored line range |
| [`terminal`](#terminal) | `execute` | `sequential` | Run a non-interactive shell command |
| [`glob`](#glob) | `read` | `parallel` | Find files by glob pattern |
| [`grep`](#grep) | `read` | `parallel` | Search file contents by regex |
| [`ls`](#ls) | `read` | `parallel` | List a directory's immediate contents |

### read

Read a UTF-8 text file. Invalid byte sequences are replaced during decoding. Every returned line is prefixed with a content-based hashline anchor in the form `<line>:<hash>|<content>`, where `<hash>` is four hex characters. Duplicate content — including blank lines — receives distinct anchors, so `edit` can target the exact displayed line.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | — | Path to the file. Prefer absolute; a relative value resolves from the agent's working directory |
| `offset` | integer | No | `0` | Number of lines to skip; `0` starts at the first line |
| `limit` | integer | No | `2000` | Maximum number of lines to return |

Example call and result:

```json
{"path": "/repo/a.txt"}
```

```text
1:2c17|alpha
2:8798|
3:987b|beta
4:478b|
5:f09f|beta
```

The result `metadata` carries `file_path`, `total_lines`, `lines_returned`, `offset`, and `truncated`.

### write

Create a new file or overwrite an existing file entirely.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Path to write. Prefer absolute; a relative value resolves from the agent's working directory |
| `content` | string | Yes | Exact complete UTF-8 text content, replacing anything already there |

### edit

Replace an inclusive line range using the hashline anchors returned by `read`. The content hash lets an anchor survive insertions or deletions elsewhere in the file. When the same content occurs more than once, the anchor's line number acts as a proximity hint and the closest occurrence is selected. An empty `new_content` deletes the range.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Path to the file. Prefer absolute; a relative value resolves from the agent's working directory |
| `start_anchor` | string | Yes | Anchor for the first line to replace, formatted `<line>:<hash>` |
| `end_anchor` | string | Yes | Anchor for the last line to replace. Use the start anchor for a single-line edit |
| `new_content` | string | Yes | New UTF-8 content for the anchored range. Empty content deletes the range |

To replace line 3 of the `read` example above:

```json
{
  "path": "/repo/a.txt",
  "start_anchor": "3:987b",
  "end_anchor": "3:987b",
  "new_content": "gamma"
}
```

Because the complete file is rewritten, an edit may normalize line endings. Edit-result diffs also display hashline anchors: removed lines use their old hashes, while added and context lines use current hashes. If an anchor hash is stale or invalid, the model-visible error includes current nearby hashline-anchored content and asks the agent to re-read before retrying.

Diffs always show every changed line. By default they include three unchanged lines around each change and collapse larger gaps into `… (+N lines)`; Ctrl+O expands the gaps.

### terminal

Execute a non-interactive shell command in the agent's working directory and return the combined stdout + stderr tail. At most 50 KiB or 2,000 lines are retained. When output is truncated, the complete output is written to a temporary file whose path appears in the result and in `metadata`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `cmd` | string | Yes | — | Non-interactive shell command to execute |
| `timeout` | integer | No | `30` | Timeout in seconds; minimum `1`, no upper bound |

Output streams through tool-update events while the command runs. Updates are throttled to at most once every 100 milliseconds, with guaranteed initial and final updates. The interactive TUI refreshes the existing result block on each update; when a long result is collapsed, its newest lines stay visible while the command runs.

Timeout and cancellation terminate the command's complete process tree. Programs may still buffer their own output — use an unbuffered mode such as `python -u` when you need immediate output.

> **Note:** Commands that require interactive input are unsupported and will hang until the timeout fires.

### glob

Find files matching a glob pattern, evaluated relative to `path`. Ripgrep's default filtering excludes hidden files and files matched by ignore rules.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | Yes | — | Glob pattern, e.g. `src/**/*.py` |
| `path` | string | No | `""` | Base directory. Empty uses the agent's working directory; a relative value resolves from Tau's process working directory |

Requires `rg` (ripgrep) on `PATH`; the tool returns an error when `rg` is unavailable.

### grep

Search for a regular expression across files. Directory searches are recursive. Ripgrep's default filtering excludes hidden files and files matched by ignore rules.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | No | `""` | Regular expression to search for |
| `path` | string | No | `""` | File or directory. Empty uses the agent's working directory; a relative value resolves from Tau's process working directory |
| `include` | string | No | `""` | Glob filter for files, e.g. `*.py` |
| `case_sensitive` | boolean | No | `true` | Whether the pattern is case-sensitive |

Requires `rg` (ripgrep) on `PATH`; the tool returns an error when `rg` is unavailable.

### ls

List a directory's immediate contents without recursing.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | No | `""` | Directory to list. Empty uses the agent's working directory; a relative value resolves from Tau's process working directory |

## Bundled Extension Tools

These tools ship with Tau but are provided by built-in extensions rather than the core tool set, so they register under the `extension` source and are present only when their extension is active. See [Extensions](extensions.md).

| Tool | Kind | Execution Mode | Extension | Purpose |
|------|------|----------------|-----------|---------|
| `todo` | `read` | `sequential` | `todo` | Maintain the session's task list |
| `web_search` | `web` | `parallel` | `web` | Search the web through a configured search engine |
| `web_fetch` | `web` | `parallel` | `web` | Fetch and extract the contents of a URL |

`web_search` accepts `query` (required), plus `mode` (`text`, `news`, `images`, `videos`, or `books`; default `text`), `max_results` (default `10`), and an optional site/region filter. `web_fetch` accepts `url` (required), an optional extraction hint, and `timeout` (default `10` seconds).

## How Tools Work

1. The model emits a tool call with a name and a parameter object.
2. The engine looks the name up in its tool table and validates the parameters against the tool's Pydantic `schema`.
3. The engine schedules the call according to the tool's [execution mode](#execution-modes).
4. `execute()` runs and returns a `ToolResult` carrying text `content`, a `metadata` dict, and an `is_error` flag.
5. The result is appended to the conversation in the model's original call order, and the model decides what to do next.

Invalid parameters and execution failures both come back to the model as an error result rather than raising, so it can correct itself and retry.

> **Security:** Tau has no tool approval prompt and no built-in sandbox. Every tool runs with the full permissions of the user account that started Tau. `ToolKind` is descriptive metadata, not a permission boundary. Run untrusted work in a container or VM.

## Tool Kinds

`ToolKind` labels a tool's semantic category. It is carried on the tool call for rendering, telemetry, and hook logic — it does **not** gate execution.

| Kind | Meaning |
|------|---------|
| `read` | Observes state without modifying it |
| `edit` | Modifies part of an existing file |
| `write` | Creates or replaces a file wholesale |
| `execute` | Runs an external process or command |
| `web` | Reaches the network |

## Execution Modes

`ToolExecutionMode` controls how the engine schedules a batch of tool calls the model emitted together.

| Mode | Behavior |
|------|----------|
| `sequential` | An ordering barrier. If any call in a batch is sequential, the whole batch runs one at a time in source order |
| `parallel` | Runs concurrently, but only when *every* call in the batch is parallel |
| `batch` | Engine-level safe mixed scheduling; this is the default policy |

The engine's own `EngineOptions.execution_mode` selects the top-level policy. `Batch` (the default) inspects each batch and downgrades to sequential execution the moment a non-parallel tool appears, because running parallel tools around a sequential one could reorder observable side effects. Setting the engine to `Sequential` forces one-at-a-time execution regardless of what the tools declare.

Tau always preserves the model's tool-call order in the resulting tool message. Completion events may arrive out of order during parallel execution, but result messages stay in source order.

## Result Rendering

For tools using `render_shell="default"`, the TUI owns result collapsing and the Ctrl+O hint. Results at or below `tool_result_preview_lines` render in full without a hint.

| Attribute | Effect |
|-----------|--------|
| `render_shell` | `"self"` (default) uses renderer output as-is; `"default"` applies the standard `└ first_line` shell with central collapse handling |
| `result_expandable` | `False` disables central collapsing; the complete rendered output always shows |
| `result_preview_lines` | Overrides the global preview threshold for this tool |
| `render_call` | Callback rendering the invocation line |
| `render_result` | Callback rendering the result body |

Custom renderers should return their complete semantic output and must not add their own expand/collapse hints.

Results render as plain text by default. A tool can opt an individual successful result into Markdown with `metadata={"_render_format": "markdown"}`; Tau renders the Markdown before applying the preview/collapse shell. Error results stay plain text.

Extensions that enrich model-facing result text can keep the original TUI text in `_display_content`, and append structured UI sections through `_extra_blocks`, where each block carries `lines` and may set its own `preview_lines` threshold.

## The Tool Registry

`ToolRegistry` is the single source of truth for registered tools. Each tool is tagged with a source so a whole group can be queried or replaced without disturbing the others.

| Source | Origin |
|--------|--------|
| `builtin` | `tau.builtins.tools.TOOLS` |
| `extension` | Tools registered by loaded extensions |
| `mcp` | Tools provided by MCP servers |
| `runtime` | Tools passed via `RuntimeConfig.tools` at session start |

```python
from tau.tool.registry import ToolRegistry

registry = ToolRegistry()
registry.register(MyTool(), source="extension")
registry.replace_source("extension", new_extension_tools)  # Atomic swap
registry.sync_to_engine(engine)                            # Push to a live engine

registry.get("web_search")        # Tool | None
registry.list(source="builtin")   # list[Tool]
registry.names()                  # set[str]
"terminal" in registry            # True
```

When a name is registered from several sources, the most recently registered layer wins; removing that layer restores the one beneath it. After `/reload`, extension tools sync to the live engine without restarting the session.

Resource bounds are enforced per tool: terminal output is capped at 50 KiB / 2,000 lines, `grep` and `glob` stream bounded ripgrep results and terminate the subprocess on cancellation or when the result cap is reached, and writes and edits are serialized per resolved path and committed with an atomic replacement.

## Constraining Tool Use

To give the agent standing instructions about which tools to use and when, add an `AGENTS.md` or `CLAUDE.md` file to your project. Tau discovers these case-insensitively from the Git repository root down to the current directory and injects them into the system prompt.

See [Project Context Files](project-context.md) for the full precedence, trust, and formatting rules.

## Next Steps

- [Creating Tools](creating-tools.md) — Implement, register, and test a custom tool
- [Extensions](extensions.md) — Package tools alongside commands, hooks, and UI
- [Project Context Files](project-context.md) — Standing instructions for tool use
- [Engine](engine.md) — The loop that schedules and executes tool calls
- [Architecture](architecture.md) — How the tool system fits together internally
