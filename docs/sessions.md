# Sessions

Tau saves every conversation as a session so you can resume work, branch from an earlier turn, and revisit paths you abandoned. Sessions are trees, not flat transcripts, and the whole tree lives in one JSONL file.

## Table of Contents

- [Session Storage](#session-storage)
- [Session Commands](#session-commands)
- [CLI Flags](#cli-flags)
- [The Session Tree](#the-session-tree)
- [Branching: `/tree`, `/fork`, and `/clone`](#branching-tree-fork-and-clone)
- [Resuming Sessions](#resuming-sessions)
- [Session File Format](#session-file-format)
- [SessionManager API](#sessionmanager-api)
- [Standalone Usage](#standalone-usage)
- [Context Compaction](#context-compaction)

## Session Storage

Sessions auto-save to `~/.tau/sessions/`, one subdirectory per project. Each session is a JSONL file.

```text
~/.tau/sessions/
├── --Users-alice-projects-myapp-3f2a91c7--/
│   ├── 2026-01-15T10-30-45-123456_01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c.jsonl
│   ├── 2026-01-15T14-22-19-654321_01912e6f-2c1a-70e3-8a4d-9b1e5c7f2d0a.jsonl
│   └── 2026-01-16T09-15-33-789012_01912ea3-5d8b-71f4-b2c3-6a9e4f0d1c8b.jsonl
└── --Users-alice-projects-other-b81de40a--/
    └── 2026-01-16T11-45-22-345678_01912ec1-9a4f-72b5-c3d6-8e1a5b0f4d2c.jsonl
```

### Directory naming

The project directory name encodes the absolute working directory plus a short hash:

```text
--<abs-path-with-separators-as-dashes>-<sha256(abs-path)[:8]>--
```

`/Users/alice/projects/myapp` becomes `--Users-alice-projects-myapp-3f2a91c7--`. The leading separator is stripped, and `/`, `\`, and `:` all become `-`.

> **The hash suffix is required.** Flattening separators is not injective — `/x/my-app` and `/x/my/app` both encode to `--x-my-app--`. The hash disambiguates them.

Legacy directories written before the hash was introduced have no suffix (`--Users-alice-projects-myapp--`). Tau still finds them: if the hashed directory does not exist but the legacy one does, the legacy directory is used, so existing sessions keep working. New projects always get the hashed form.

### File naming

```text
<timestamp>_<session-id>.jsonl
```

`<timestamp>` is `strftime("%Y-%m-%dT%H-%M-%S-%f")`; `<session-id>` is a UUIDv7, so IDs sort chronologically on their own.

### Overriding the location

| Method | Scope |
|--------|-------|
| `tau --session-dir PATH` | This run |
| `session_dir` in `settings.json` | All runs (supports `~`) |
| `RuntimeConfig(session_dir=...)` | Programmatic |

### Related paths

| Path | Contents |
|------|----------|
| `~/.tau/sessions/` | All session files, per project |
| `~/.tau/logs/<session-id>.log` | Run log for that session |
| `<session-file>.lock` | Per-session file lock held during writes |
| `<session-file>.bak` | One-time backup, written only if the file had unparseable lines |

## Session Commands

Session management from inside the TUI:

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/new` | — | Start a fresh session |
| `/resume` | — | Browse and resume a past session interactively |
| `/tree` | — | Navigate the session tree and switch branch |
| `/fork` | `<entry_id>` *(required)* | Branch the session tree at a given entry ID |
| `/clone` | — | Duplicate the current session at the current position |
| `/session` | — | Show session info and stats |
| `/compact` | `[instructions]` | Summarize and compact the current session context |
| `/clear` | — | Clear the message list |

Other built-in commands: `/model`, `/effort`, `/theme`, `/settings`, `/extensions`, `/reload`, `/login`, `/logout`, `/copy`, `/todos`, `/help` (alias `/?`), `/quit` (aliases `/q`, `/exit`).

> There is no `/name`, `/export`, or `/share` command. Session naming is available only through the `--name` CLI flag, the RPC API, and the extension API. (`/name` shown in `/help` refers to expanding a prompt template called *name*, not to renaming a session.)

## CLI Flags

```bash
tau -r                       # Resume the most recent session for this directory
tau --resume abc123          # Resume the session whose ID contains "abc123"
tau --fork abc123            # Fork that session into a new one
tau --ephemeral              # Don't save this session to disk
tau -e                       # Same, short form
tau --name "Fix login bug"   # Set the session display name at startup
tau --session-dir /tmp/tau   # Use a different session storage root
```

| Flag | Argument | Description |
|------|----------|-------------|
| `--resume`, `-r` | `[ID]` optional | Resume a session. Omit the ID for the most recent |
| `--fork` | `ID` | Fork an existing session by ID into a new session |
| `--ephemeral`, `-e` | — | Do not persist this session |
| `--name` | `NAME` | Session display name |
| `--session-dir` | `PATH` | Session storage directory |

`--resume` and `--fork` are mutually exclusive. IDs are matched as substrings against session filenames, so a short prefix is enough.

> **`-c` is `--cwd`, not "continue".** There is no `--continue` flag and no `--session` flag; use `-r` and `--session-dir` respectively.

## The Session Tree

Every entry carries an `id` and a `parent_id`. The current position is the active leaf. Branching adds a new child to an earlier entry instead of creating a new file, so alternatives live side by side.

```text
├─ user: "Add retry logic to the client"
│  └─ assistant: "I'll wrap the request in..."
│     └─ tool_result: edit client.py
│        ├─ user: "Use exponential backoff"        ← branch 1
│        │  └─ assistant: "Switching to backoff..."
│        │     └─ user: "Cap it at 30s"            ← active leaf
│        └─ user: "Actually, make it configurable" ← branch 2
│           └─ assistant: "Adding a retry option..."
```

When you switch branches, Tau appends a `leaf` entry pointing at the new active node, so the position survives a restart. Parent traversal rejects cycles in malformed data, and entries whose parent is missing are rendered as separate roots so orphaned history stays visible.

### `/tree` controls

| Key | Action |
|-----|--------|
| ↑ / ↓ | Move selection |
| ← / → | Fold / unfold |
| Ctrl+← / Ctrl+→ (or Alt+←/→) | Page up / page down |
| Page Up / Page Down | Page up / page down |
| Enter or Tab | Select entry |
| Escape | Cancel |
| Shift+L | Set or clear a label on the selected entry |
| Shift+T | Toggle label timestamps |
| Ctrl+F | Cycle filter mode |
| Ctrl+D | Reset filter to default |
| Ctrl+T | Toggle the *no-tools* filter |
| Ctrl+U | Toggle the *user-only* filter |
| Ctrl+L | Toggle the *labeled-only* filter |
| Ctrl+A | Toggle the *all* filter |
| Backspace | Delete last search character |
| Any printable key | Fuzzy-search entries |

Filter modes are `default`, `no-tools`, `user-only`, `labeled-only`, and `all`.

### Selection behavior

Selecting a plain user message moves the leaf to that message's **parent** and restores the message text into the editor, so you can edit and resubmit to create a new branch. Selecting any other entry moves the leaf to that entry and leaves the editor empty.

Assistant turns with unanswered tool calls cannot be selected — pick the tool result or a later message instead.

### Branch summaries

When `/tree` moves you away from a branch, Tau can summarize the path you are leaving and attach that summary at the new position, so context is not lost. The TUI offers **No summary** / **Summarize** before navigating.

- Programmatic callers opt in with `runtime.navigate_tree(target_id, summarize=True)`.
- The summary captures goal, progress, key decisions, next steps, and files read/modified.
- It is stored as a `branch_summary` entry under the destination node and injected into that branch's context.
- If summarization fails or is aborted, navigation still completes and Tau reports it.
- Extensions can return a finished summary from `session_before_tree` to bypass Tau's own model call. See [Extensions](extensions.md).

## Branching: `/tree`, `/fork`, and `/clone`

| Feature | `/tree` | `/fork` | `/clone` |
|---------|---------|---------|----------|
| Output | Same session file | Same session file | New session file |
| Input | Interactive tree picker | `<entry_id>` argument | Current position, no argument |
| View | Full tree, filterable | — | — |
| Branch summary | Optional | No | No |
| Typical use | Explore alternatives in place | Branch at a known entry ID | Snapshot before a risky change |

Both `/tree` and `/fork` grow a new branch inside the current JSONL file — the original path is preserved alongside it. Only `/clone` produces a separate file; the clone records the original as its `parent_session`, and the two evolve independently.

When a branch is extracted into a new file, label entries are recreated at the end of the extracted path and retained entries are re-chained, so removing a label from the middle cannot orphan parent IDs.

## Resuming Sessions

`/resume` opens a searchable picker for past sessions; `tau -r` resumes the most recent without prompting.

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| Enter | Select session |
| Tab | Toggle scope: current folder ↔ all projects |
| Ctrl+R | Cycle sort: Recent → Oldest → Name |
| Ctrl+D | Start delete confirmation |
| Enter / Escape | Confirm / cancel the delete |
| Escape | Cancel the picker |
| Any printable key | Search by name, ID, or path |
| Backspace | Delete last search character |

The picker loads lazily in pages of 20 and always excludes the active session. Deleting removes the file and its associated media; the active session cannot be deleted. There is no rename in the picker.

After resuming, `/new` always starts an empty session — the startup resume selection is not reused for later session changes.

### Session info

`/session` shows a read-only overlay (Escape to close) with the session file path, session ID, branch depth, and user / assistant / tool-call counts for the current branch.

### Deleting and backing up

```bash
rm ~/.tau/sessions/--Users-alice-projects-myapp-3f2a91c7--/2026-01-15T10-30-45-123456_01912e4a-*.jsonl
cp -r ~/.tau/sessions ~/backups/tau-sessions-$(date +%Y%m%d)
```

## Session File Format

One JSON object per line. The first line is always the header; every following line is a session entry.

### Header

```json
{"type":"session","version":3,"id":"01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c","cwd":"/home/user/project","timestamp":1718000000.0}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"session"` | Always `"session"` |
| `version` | `int` | Current version is `3`. Opening a newer version fails |
| `id` | `str` | Session UUIDv7 |
| `timestamp` | `float` | Unix seconds |
| `cwd` | `Path` | Working directory the session was started in |
| `parent_session` | `Path \| None` | Source session file when created by `/clone` or `--fork` |

### Entry types

Every entry shares `id` (8 hex chars), `timestamp` (Unix seconds, float), and `parent_id` (`null` at the root).

| `type` | Key fields | Description |
|--------|-----------|-------------|
| `message` | `message`, `meta` | A user, assistant, tool, or terminal message |
| `custom_message` | `custom_type`, `content`, `display`, `details` | Extension-injected message; participates in LLM context |
| `custom` | `custom_type`, `data` | Extension state; **not** shown in the TUI and **not** sent to the model |
| `compaction` | `summary`, `first_kept_entry_id`, `tokens_before`, `details` | Context compaction checkpoint |
| `branch_summary` | `from_id`, `summary`, `details`, `from_hook`, `label` | Summary of an abandoned branch |
| `leaf` | `target_id` | Navigation record marking the new active node |
| `label` | `target_id`, `label` | Bookmark on an entry; `label: null` clears it |
| `thinking_level_change` | `thinking_level` | Thinking/effort level changed |
| `model_change` | `model_id`, `provider_id` | Model or provider switched |
| `session_info` | `name` | Session display name |

### Example

```jsonl
{"type":"session","version":3,"id":"01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c","cwd":"/home/user/myproject","timestamp":1718000000.0}
{"type":"model_change","id":"a1b2c3d4","parent_id":null,"timestamp":1718000000.5,"model_id":"claude-sonnet-4-6","provider_id":"anthropic"}
{"type":"message","id":"e5f6a7b8","parent_id":"a1b2c3d4","timestamp":1718000001.0,"message":{"role":"user","contents":[{"type":"text","content":"Fix the retry bug"}]}}
{"type":"message","id":"i9j0k1l2","parent_id":"e5f6a7b8","timestamp":1718000005.0,"message":{"role":"assistant","contents":[{"type":"text","content":"Looking at the client..."}]}}
{"type":"label","id":"c1d2e3f4","parent_id":"i9j0k1l2","timestamp":1718000006.0,"target_id":"i9j0k1l2","label":"before-refactor"}
{"type":"compaction","id":"m3n4o5p6","parent_id":"c1d2e3f4","timestamp":1718000010.0,"summary":"## Goal\n...","first_kept_entry_id":"i9j0k1l2","tokens_before":94000}
{"type":"branch_summary","id":"q7r8s9t0","parent_id":"e5f6a7b8","timestamp":1718000020.0,"from_id":"i9j0k1l2","summary":"The user explored..."}
{"type":"leaf","id":"u1v2w3x4","parent_id":"m3n4o5p6","timestamp":1718000030.0,"target_id":"i9j0k1l2"}
```

### Durability

Writes are taken under a per-session `FileLock` and committed with an atomic replace. Appends are pure appends; only removals and branch extraction rewrite the whole file. A rewrite merges the latest on-disk history with local changes by entry ID, so a stale in-memory view cannot discard another process's entries.

Unparseable lines are skipped with a warning on read. Before a rewrite would drop them, the original file is copied once to `<name>.jsonl.bak`. Opening an empty, header-less, or newer-version file raises without modifying it.

## SessionManager API

`SessionManager` (`tau.session.manager`) owns one session file: its entries, indices, and tree.

### Constructor and factories

```python
SessionManager(cwd, session_dir=None, session_file=None, persist=True)
```

| Factory | Description |
|---------|-------------|
| `SessionManager.create(cwd, session_dir=None)` | New session in the project's session directory |
| `SessionManager.open(path, session_dir=None, cwd_override=None)` | Load an existing session file |
| `SessionManager.continue_recent(cwd, session_dir=None)` | Most recent session for `cwd`, or a new one |
| `SessionManager.in_memory(cwd=None)` | No disk persistence |
| `SessionManager.fork_from(source, target_cwd, session_dir=None)` | Copy a session into a new file recording the source as parent |

### Listing

| Method | Returns | Description |
|--------|---------|-------------|
| `SessionManager.list(cwd, session_dir=None, on_progress=None)` | `list[SessionInfo]` | Sessions for one project, newest first |
| `SessionManager.list_all(on_progress=None)` | `list[SessionInfo]` | Sessions across all projects, newest first |
| `SessionManager.pager(cwd, session_dir=None)` | `SessionPager` | Incremental newest-first pager for one project |
| `SessionManager.all_pager()` | `SessionPager` | Incremental pager across all projects |

`SessionInfo` carries `path`, `id`, `cwd`, `name`, `parent_session`, `created`, `modified`, and `message_count`.

### Appending

Every `append_*` method returns the new entry's ID.

| Method | Writes |
|--------|--------|
| `append_message(message, meta=None)` | `message` |
| `append_model_change(model_id, provider_id)` | `model_change` |
| `append_thinking_level_change(thinking_level)` | `thinking_level_change` |
| `append_label_change(target_id, label=None)` | `label` (pass `None` to clear) |
| `append_session_info(name)` | `session_info` |
| `append_custom_info(custom_type, data=None)` | `custom` |
| `append_custom_message(custom_type, content, display=True, details=None)` | `custom_message` |
| `append_compaction(summary, first_kept_entry_id, tokens_before, details=None)` | `compaction` |
| `append_branch_summary(from_id, summary, details=None, from_hook=False, label=None)` | `branch_summary` |

### Tree navigation and inspection

| Method | Returns | Description |
|--------|---------|-------------|
| `get_leaf_id()` | `str \| None` | Current position |
| `get_leaf_entry()` | `SessionEntry \| None` | Entry at the current position |
| `get_entry(id)` | `SessionEntry \| None` | Entry by ID |
| `get_entries()` | `list[SessionEntry]` | All entries, header excluded |
| `get_children(parent_id)` | `list[SessionEntry]` | Direct children, sorted by timestamp |
| `get_branch(from_id=None)` | `list[SessionEntry]` | Root → leaf path |
| `get_tree()` | `list[SessionTreeNode]` | Full tree, children sorted by timestamp |
| `get_label(id)` | `str \| None` | Label on an entry |
| `get_header()` | `SessionHeader \| None` | Session header |
| `get_session_name()` | `str \| None` | Most recent display name |
| `find_last_assistant_message()` | `AssistantMessage \| None` | Latest assistant message on the branch |
| `build_session_context()` | `SessionContext` | Messages, thinking level, model, and provider for the LLM |

### Mutation

| Method | Description |
|--------|-------------|
| `branch(from_id)` | Move the leaf to an earlier entry, writing a `leaf` entry |
| `branch_with_summary(branch_from_id, summary, details=None, from_hook=False)` | Move the leaf and attach a branch summary |
| `reset_leaf()` | Clear the leaf pointer |
| `remove_last_message(role=None)` | Remove the message at the current leaf if it matches `role` |
| `create_branched_session(leaf_id)` | Extract the branch to a new session file and switch to it |
| `new_session(options=None)` | Start a new session; `SessionOptions(id, parent_session)` |
| `set_session(session_file)` | Load or initialize a session from a path |
| `enable_persist()` | Switch an in-memory session to persisting, flushing buffered entries |

`build_session_context()` walks the current branch, applies the most recent compaction by dropping everything before `first_kept_entry_id`, converts entries to messages, and prepends the compaction summary. Attributes `cwd`, `session_id`, `session_file`, `session_dir`, `persist`, `leaf_id`, `entries`, and `by_id` are readable directly.

## Standalone Usage

`tau.session` works on its own. You can read, inspect, and write session files with no runtime, no model, no API key, and no network.

```python
import asyncio
from pathlib import Path

from tau.message.types import AssistantMessage, UserMessage
from tau.session.manager import SessionManager
from tau.session.types import MessageEntry


async def main() -> None:
    # 1. Write a session to a directory of your choosing.
    workspace = Path.cwd()
    manager = SessionManager(cwd=workspace, session_dir=Path("/tmp/tau-sessions"))

    root = manager.append_message(UserMessage.from_text("What does this project do?"))
    manager.append_message(AssistantMessage.from_text("It is a coding agent CLI."))
    manager.append_label_change(root, "opening-question")

    # 2. Branch from the first message and take a different path.
    manager.branch(root)
    manager.append_message(AssistantMessage.from_text("It is a terminal agent framework."))

    print("session file:", manager.session_file)
    print("session id:  ", manager.session_id)

    # 3. Walk the tree.
    def walk(nodes, depth: int = 0) -> None:
        for node in nodes:
            entry = node.entry
            label = f"  [{node.label}]" if node.label else ""
            marker = "  <- leaf" if entry.id == manager.get_leaf_id() else ""
            print(f"{'  ' * depth}- {entry.type} {entry.id}{label}{marker}")
            walk(node.children, depth + 1)

    walk(manager.get_tree())

    # 4. Reopen the file and rebuild the LLM-facing context for the active branch.
    reopened = SessionManager.open(manager.session_file)
    context = reopened.build_session_context()
    print(f"{len(context.messages)} messages on the active branch")
    for message in context.messages:
        print(" ", message.role, message.contents)

    # 5. Read raw entries without a manager at all.
    for entry in reopened.get_entries():
        if isinstance(entry, MessageEntry):
            print("raw:", entry.id, entry.message.role)


asyncio.run(main())
```

`main()` is `async` only for symmetry with the rest of the API — `SessionManager` itself is fully synchronous.

Standalone, `tau.session` does **not** call a model, so `/compact` and branch summaries are unavailable: `compact()` and `generate_branch_summary()` in `tau.session.compaction` and `tau.session.branch_summarization` both require a `TextLLM`. It also does not load settings, extensions, or tools. For that layer, see [Python API](python-api.md). For direct parsing without a manager, `tau.session.utils.read_session_file(path)` returns the validated entry list, and `get_default_project_session_dir(cwd)` resolves a project's session directory.

## Context Compaction

Long sessions eventually fill the model's context window. Tau summarizes the older portion and keeps the recent messages verbatim.

### How it works

1. **Cut-point detection** — Tau walks backwards accumulating token estimates until it has preserved `keep_recent_tokens` worth of messages. It never cuts between a tool call and its result; a cut landing inside a turn causes that turn's prefix to be summarized separately.
2. **Summarization** — messages before the cut are sent to the model with a structured prompt (Goal / Progress / Decisions / Next Steps / Critical Context). The result is stored as a `compaction` entry and prepended to context in `<context-summary>` tags.
3. **Iterative merging** — when a previous summary exists, it is included in the new prompt so history is never dropped.

### When it runs

The trigger is `context_tokens >= context_window - reserve_tokens`, checked at three points:

1. **Pre-flight** — before sending a turn, catching resumed or already-oversized sessions.
2. **Post-task** — after the agent becomes idle.
3. **Overflow recovery** — if a request still fails with a provider context-overflow error, Tau drops the failed response, compacts, and retries the turn **once**. If it overflows again, Tau surfaces a message instead of looping.

Usage is estimated from the last response's reported usage plus a chars/4 estimate of anything after it, so the trigger reflects real provider accounting. Provider cache counters are normalized so cached input is not double-counted.

Three guards keep this stable:

- **Stale-anchor guard** — skips re-triggering when the usage anchor predates the latest compaction, so Tau does not compact on every subsequent turn.
- **Circuit breaker** — after 3 consecutive automatic failures, Tau stops auto-compacting for the session and reports it. `/compact` still works.
- **Model-switch guard** — usage reported by a previous model is discarded after a switch, falling back to a full heuristic estimate.

### Manual compaction

```text
/compact
/compact focus on the database migration work
```

Arguments are appended to the summarization prompt as `Additional focus: <text>`. `/compact` is hidden when compaction is disabled, and reports `Nothing to compact` when the conversation is too short.

### Configuration

Under the `compaction` key in `settings.json`:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `enabled` | `bool` | `true` | Enable automatic compaction |
| `reserve_tokens` | `int` | `16384` | Headroom to keep free; compaction triggers below it |
| `keep_recent_tokens` | `int` | `20000` | Approximate budget of recent messages kept verbatim |

```json
{
  "compaction": {
    "enabled": true,
    "reserve_tokens": 8192,
    "keep_recent_tokens": 32000
  }
}
```

Both token settings are clamped at runtime to the active model's input limit, always leaving room for the generated summary. Disabling automatic compaction also disables overflow-triggered compaction and retry — provider context-overflow errors are returned directly.

Extensions can intercept `before_compaction` to replace the default summarization entirely. See [Extensions](extensions.md).

## Next Steps

- [Python API](python-api.md) — driving sessions programmatically
- [Usage Guide](usage.md) — session commands in interactive mode
- [Messages & Context](messages.md) — how messages are structured
- [Settings](settings.md) — session and compaction configuration
