# Sessions

This page explains how Tau manages sessions, persistence, and branching.

## What Is a Session?

A session is a conversation between you and the agent. Each session includes:

- All messages (user inputs and agent responses)
- Tool calls and results
- Model and provider choices
- Settings and theme choices
- Metadata (creation time, working directory)

Sessions are saved to `~/.tau/sessions/` in a per-project subdirectory so sessions from different projects never mix.

Set `session_dir` in settings, or pass `--session-dir PATH`, to override this
location. `tau --resume ID` resumes a matching session, `tau --fork ID` creates
a new session from it, and `--name NAME` assigns a display name at startup.

## Session Files

Sessions are stored per-project. The working directory is encoded into a safe subdirectory name:

```text
~/.tau/sessions/
├── --Users-alice-projects-myapp--/
│   ├── 2024-01-15T10-30-45-123456_01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c.jsonl
│   ├── 2024-01-15T14-22-19-654321_01912e6f-2c1a-70e3-8a4d-9b1e5c7f2d0a.jsonl
│   └── 2024-01-16T09-15-33-789012_01912ea3-5d8b-71f4-b2c3-6a9e4f0d1c8b.jsonl
└── --Users-alice-projects-other--/
    └── 2024-01-16T11-45-22-345678_01912ec1-9a4f-72b5-c3d6-8e1a5b0f4d2c.jsonl
```

Each filename is `<timestamp>_<session-id>.jsonl`, where the timestamp is `strftime("%Y-%m-%dT%H-%M-%S-%f")` and the session ID is a UUIDv7.

The subdirectory name is derived from the absolute path of the working directory (`/Users/alice/projects/myapp` → `--Users-alice-projects-myapp--`). This keeps sessions from different projects completely separate, so browsing with `/resume` always shows only the sessions relevant to the current project.

Files are in JSONL format (one JSON object per line), allowing efficient appending and compression.

Opening an existing empty, invalid, or unsupported future-version session
fails without modifying the file. Persistence errors are reported to the
caller instead of allowing in-memory state to appear saved when the disk write
failed.

When a branch is extracted into a new session, label entries are recreated at
the end of the extracted path. Retained entries are re-chained so removing a
label from between conversation entries cannot leave orphaned parent IDs.

## Resume Sessions

### Continue Last Session

Resume your most recent session:

```bash
tau --resume
```

Or the short form:

```bash
tau -r
```

After resuming, `/new` always starts an empty session; the startup resume
selection is not reused for later session changes.

### Browse Sessions

Choose from a list of previous sessions interactively:

Inside a session, use the slash command:

```bash
/resume
```

This opens a searchable picker showing all past sessions for the current project directory.

### Specific Session

Resume a specific session by ID:

```bash
tau --resume abc123
```

## Start New Sessions

Start a new session in the current working directory:

```bash
tau
```

### Named Sessions

Name a session when starting:

```bash
tau --name "Fix login bug"
```

The TUI does not currently expose a rename command. Extensions and RPC clients
can update the session name through their respective APIs.

## Ephemeral Sessions

Run tau without saving anything to disk:

```bash
tau --ephemeral
```

Or the short form:

```bash
tau -e
```

Useful for one-off queries where you don't need the conversation history.

## Session Tree

Every session is a **tree**, not a flat list. When you branch with `/fork` or navigate to a different point in history, tau records a `leaf` entry that points to the new active node. The full conversation history — including all branches — lives in a single JSONL file.

Parent traversal rejects cycles in malformed session data. Tree rendering
treats missing-parent entries as separate roots so recoverable orphaned history
remains visible.

```text
root
 └── msg A (user)
      └── msg B (assistant)
           ├── msg C (user)          ← branch 1
           │    └── msg D (assistant)
           └── msg E (user)          ← branch 2 (current leaf)
                └── msg F (assistant)
```

Each entry has a `parent_id` that links it to its parent. `leaf` entries record navigation — when you switch to a different branch, tau writes a `leaf` entry pointing to the target node, so the active position survives restarts.

## Session Branching

If you want to explore multiple directions from a conversation point:

### Fork from a Specific Message

Use `/tree` to open the session tree navigator, then press Enter on any message node to branch from that point. Alternatively, use `/fork <entry-id>` directly if you know the entry ID.

Forking stays within the same session file — the tree grows a new branch. Both the original path and the new branch coexist in the same JSONL file.

### Clone Current Branch

Duplicate the entire current branch into a new session file and switch into it:

```text
/clone
```

Both sessions start in an identical state. Changes in one do not affect the other. Useful for running parallel explorations or creating a safe copy before a risky change.

### Navigating Branches

Use `/tree` to open the interactive branch navigator. It shows every message node in the session tree, indented by depth, with the current leaf marked `(current)`. Press Enter on any node to navigate to that branch point.

When you navigate to a different branch, Tau can generate a **branch summary**
of the path you're leaving, so context from the abandoned branch isn't lost.
The TUI asks whether to summarize unless prompting is disabled. When generated,
the summary is attached to the destination branch and injected into its context
as a `[Branch Summary]` block.

#### Branch summary behaviour

- Offered during TUI navigation; API callers opt in with `summarize=True`.
- Captures: goal, progress, key decisions, next steps, and files read/modified.
- Stored as a `branch_summary` entry under the destination node and included in that branch's context.
- Uses the active model's input limit and a bounded prompt. If summarization fails, navigation still completes without a summary and Tau reports the failure.
- Extensions can return a complete summary and metadata from
  `session_before_tree`, bypassing Tau's default summary model call. See
  [Extensions](extensions.md#event-reference).

### Resuming a Past Session

Use `/resume` to open a searchable list of past sessions for the current directory, sorted by last modified. Type to filter, arrow keys to navigate, Enter to switch. The current session is excluded from the list.

## Session Information

View details about the current session:

```text
/session
```

Shows a read-only overlay (Esc to close) with:

- **File** — path to the session JSONL file
- **ID** — session identifier
- **Branch depth** — number of entries on the current branch
- **User / Assistant / Tool calls** — message counts on the current branch

## Session Deletion

To delete a session, remove the session file:

```bash
rm ~/.tau/sessions/path_to_project/2024-01-15T10-30-45-123456_01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c.jsonl
```

## Backup Sessions

Sessions are stored in `~/.tau/sessions/`. Back up this directory regularly:

```bash
cp -r ~/.tau/sessions ~/backups/tau-sessions-$(date +%Y%m%d)
```

## Session Format

Session files are JSONL — one JSON object per line. The first line is always a session header; every subsequent line is a session entry.

### Header

```json
{"type": "session", "version": 3, "id": "01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c", "cwd": "/home/user/project", "timestamp": 1718000000.0}
```

### Entry types

All entries share `id`, `timestamp`, and `parent_id` fields. `parent_id` is null for root entries.

| `type` | Key fields | Description |
|--------|-----------|-------------|
| `message` | `message` | A user, assistant, or tool message (full message object) |
| `custom_message` | `custom_type`, `content`, `display` | Extension-injected message displayed in the TUI |
| `compaction` | `summary`, `first_kept_entry_id`, `tokens_before` | Context compaction checkpoint — summary of everything before `first_kept_entry_id` |
| `branch_summary` | `from_id`, `summary`, `details`, `from_hook` | Branch navigation summary — captures context from an abandoned conversation path |
| `leaf` | `target_id` | Navigation record — points to the current active node after a branch switch |
| `label` | `target_id`, `label` | Human-readable name attached to an entry (shown in tree view) |
| `thinking_level_change` | `thinking_level` | User changed the extended thinking budget level |
| `model_change` | `model_id`, `provider_id` | User switched models |
| `session_info` | `name` | Session display name set at startup, by RPC, or by an extension |
| `custom` | `custom_type`, `data` | Extension-specific metadata (not shown in TUI, not sent to LLM) |

### Example

```jsonl
{"type": "session", "version": 3, "id": "01912e4a-6b3f-7a21-9c88-4f2e6d1a9b3c", "cwd": "/home/user/myproject", "timestamp": 1718000000.0}
{"type": "message", "id": "e5f6g7h8", "parent_id": null, "timestamp": 1718000001.0, "message": {"role": "user", ...}}
{"type": "message", "id": "i9j0k1l2", "parent_id": "e5f6g7h8", "timestamp": 1718000005.0, "message": {"role": "assistant", ...}}
{"type": "compaction", "id": "m3n4o5p6", "parent_id": "i9j0k1l2", "summary": "## Goal\n...", "first_kept_entry_id": "i9j0k1l2", "tokens_before": 94000, "timestamp": 1718000010.0}
{"type": "branch_summary", "id": "q7r8s9t0", "parent_id": "e5f6g7h8", "from_id": "i9j0k1l2", "summary": "The user explored...", "timestamp": 1718000020.0}
{"type": "leaf", "id": "u1v2w3x4", "parent_id": "m3n4o5p6", "target_id": "i9j0k1l2", "timestamp": 1718000030.0}
```

## Session Storage Location

Sessions are stored in `~/.tau/sessions/<encoded-cwd>/`. Each project gets its own subdirectory, so sessions are always scoped to the directory you started tau from.

To use a custom root location:

```bash
tau --session-dir /path/to/custom/sessions
```

## Context Compaction

Long sessions eventually fill the model's context window. Tau handles this automatically through **context compaction**: when the context gets too full, Tau summarizes the older portion of the conversation and replaces it with that summary, keeping the most recent messages intact.

### How It Works

1. **Cut-point detection** — Tau walks backwards from the most recent message, accumulating token estimates, until it has identified the `keep_recent_tokens` worth of messages to preserve. It never cuts in the middle of a tool call/result pair; if the cut would land inside a turn, the turn's prefix is summarised separately.
2. **Summarisation** — the messages before the cut point are sent to the model with a structured summarisation prompt (Goal / Progress / Decisions / Next Steps / Critical Context). The resulting summary is stored in the session file as a `compaction` entry and prepended to the context (wrapped in `<context-summary>` tags) on the next turn.
3. **Iterative merging** — if compaction has happened before, the new summary prompt includes the previous summary so history is never lost.

### When Compaction Runs

Token usage is estimated from the **last successful response's reported usage** plus a chars/4 estimate of anything after it, so the trigger reflects real provider accounting rather than a pure guess. Provider cache counters are normalized so cached input is not counted twice. Before the first response, and after switching models, the estimate also includes the system prompt and tool schemas.

The threshold is `context_tokens >= context_window - reserve_tokens`. Tau checks this at three points so context is trimmed before it can overflow:

1. **Pre-flight** — before sending a new turn. Catches resumed or already-oversized sessions that would otherwise fail on the very first request.
2. **Post-task** — after the agent finishes the current prompt and becomes idle.
3. **Overflow recovery (reactive backstop)** — if a request still fails with a provider *context-overflow* error, Tau catches it, drops the failed response, compacts, and **retries the turn once** automatically. This is bounded to a single attempt per turn — if it overflows again, Tau surfaces a message asking you to reduce context or switch to a larger-context model rather than looping.

Two guards keep this stable:

- **Stale-anchor guard** — immediately after a compaction, the kept messages still carry their pre-compaction (large) usage numbers. Tau skips re-triggering when the usage anchor predates the latest compaction, so it won't compact on every subsequent turn.
- **Circuit breaker** — if automatic compaction fails repeatedly (3×), Tau stops auto-compacting for the session and surfaces a notice instead of failing silently. You can still run `/compact` manually.
- **Model-switch guard** — usage reported by the previous model is discarded after a model change. Tau falls back to a full heuristic estimate against the new model's context window instead of skipping the check.

### Manual Compaction

Trigger compaction yourself at any time:

```text
/compact
```

Useful before a long task when you want to free up context headroom proactively.

### Configure Compaction

Compaction is controlled by three settings under the `compaction` key in `settings.json`:

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable or disable automatic compaction |
| `reserve_tokens` | `16384` | Tokens to keep free at the top of the context window; compaction triggers when headroom falls below this |
| `keep_recent_tokens` | `20000` | Approximate token budget for the recent messages that are kept verbatim after compaction |

At runtime, Tau clamps `reserve_tokens` and `keep_recent_tokens` to the active
model's input limit. It always leaves room for the generated summary, preventing
small-context models from retaining more history than they can accept.

Disabling automatic compaction also disables overflow-triggered compaction and
retry. Provider context-overflow errors are returned directly in that mode.

Example — tighten the trigger and keep more recent history:

```json
{
  "compaction": {
    "enabled": true,
    "reserve_tokens": 8192,
    "keep_recent_tokens": 32000
  }
}
```

To disable automatic compaction entirely (you can still run `/compact` manually):

```json
{
  "compaction": {
    "enabled": false
  }
}
```

### Custom Compaction via Extensions

Extensions can intercept the `before_compaction` event to replace the default LLM summarisation with their own algorithm — for example, using a cheaper model, a different prompt, or a completely custom strategy. See [Custom Compaction](extensions.md#custom-compaction) in the Extensions guide.

### Compaction in Session Files

Each compaction is saved as a `compaction` entry in the JSONL session file:

```json
{"type": "compaction", "summary": "...", "tokens_before": 94000, "first_kept_entry_id": "...", "timestamp": "..."}
```

This means you can always reload a session and Tau will reconstruct the correct context, even across multiple compaction cycles.

## Next Steps

- [Usage Guide](usage.md) - Session commands in interactive mode
- [Messages & Context](messages.md) - How messages work
- [Settings](settings.md) - Session configuration options
