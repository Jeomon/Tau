# Usage Guide

This page covers day-to-day work in Tau's interactive mode. For flags, subcommands, and the non-interactive modes, see [CLI Reference](cli-reference.md).

## Table of Contents

- [Interactive Mode](#interactive-mode)
- [Editor Features](#editor-features)
- [Slash Commands](#slash-commands)
- [Command Details](#command-details)
- [Message Queue](#message-queue)
- [Sessions](#sessions)
- [Context Files](#context-files)
- [Keyboard Shortcuts](#keyboard-shortcuts)

## Interactive Mode

Run `tau` to open the terminal UI. The screen is composed of stacked zones, top to bottom:

```text
┌─ header ──────────────  # session, model, and startup info
│  messages               # conversation, tool calls, tool results
│  spinner                # shown while the agent is working
│  pending queue          # queued steering / follow-up messages
│  status                 # extension status slots
├─ divider ─────────────
│  editor                 # where you type
├─ divider ─────────────
│  pickers / palette      # file picker, command palette, selectors
└─ footer ──────────────  # working directory, token usage, model
```

Extensions can add their own widgets above or below the editor, and inline selectors temporarily replace the picker zone.

Markdown responses render with terminal-readable inline and display LaTeX math. Named links show their label rather than repeating the URL; in terminals with OSC 8 support, use the terminal's link gesture (commonly Alt+click or Cmd+click) to open them. Markdown images render as linked placeholders when inline image display is unavailable; local image paths link through an absolute `file://` URI. Long-running terminal tools update their existing output block while streaming instead of printing a new block per chunk.

## Editor Features

| Feature | How |
|---------|-----|
| Submit message | **Enter** |
| New line | **Shift+Enter**, or `\` followed by Enter |
| File reference | Type `@` to browse and insert a file path (↑↓ to browse, Tab to select) |
| Slash command | Type `/` to open the command palette |
| Prompt template | `/name [args]` expands a template and sends it |
| Shell command | `!command` runs immediately without involving the model |
| Paste | Use the terminal's own paste gesture; bracketed paste is decoded automatically |
| Large pastes | Collapsed into a placeholder reference rather than filling the editor |
| History | **Up** / **Down** at the first/last line browses previous inputs |
| Undo / redo | **Ctrl+Z** / **Ctrl+Y** (word-level grouping) |

Pasting an image, audio, or video file attaches it as media instead of text.

## Slash Commands

Type `/` to open the command palette. Commands are fuzzy-searchable — type a few characters to filter.

Most commands wait until the active turn finishes. UI-only and read-only commands — `/theme`, `/settings`, `/session`, `/copy`, `/help` — run immediately even while the agent is busy. This dispatch is separate from Enter steering and Alt+Enter follow-up messages.

### Session

| Command | Description |
|---------|-------------|
| `/new` | Start a fresh session |
| `/resume` | Browse and resume a past session interactively |
| `/fork <entry_id>` | Branch the session tree at a given entry ID (argument required) |
| `/tree` | Navigate the session tree and switch to a different branch |
| `/clone` | Duplicate the current session at the current position |
| `/compact [instruction]` | Summarize and compact the current session context |
| `/session` | Show session info and stats |

### Model and Appearance

| Command | Description |
|---------|-------------|
| `/model [modality]` | Switch models for any modality: `text`, `voice`, `speak`, `image`, `video` |
| `/effort` | Set the thinking effort level for the current model |
| `/theme` | Change the UI theme (interactive picker) |
| `/settings` | Show current settings |

### Authentication

| Command | Description |
|---------|-------------|
| `/login` | Save an API key for a provider |
| `/logout` | Remove stored credentials for a provider |

### Other

| Command | Aliases | Description |
|---------|---------|-------------|
| `/clear` | | Clear the message list |
| `/copy` | | Copy the last assistant message to the clipboard |
| `/reload` | | Reload extensions, themes, and prompt appends |
| `/extensions` | | Enable or disable extensions by scope |
| `/help` | `/?` | List all commands and keyboard shortcuts |
| `/quit` | `/q`, `/exit` | Exit Tau |

Extensions, skills, and prompt templates also register commands and appear in the palette. Type `/` and browse to see everything available in your install.

## Command Details

### `/model`

Opens a model picker for the given modality, defaulting to `text`. Speak models that declare voices open a second voice picker after model selection.

```text
/model              # pick a text model
/model image        # pick an image-generation model
```

### `/resume`

Opens a searchable session picker showing past sessions for the current directory, sorted by last modified. Each row shows filename, timestamp, message count, and working directory. Type to filter, arrows to navigate, Enter to switch.

### `/tree`

Opens the session tree navigator showing every message node in the current session, indented by depth. Each row shows the entry ID prefix and the start of the message; `(current)` marks the active leaf. Enter navigates to the selected branch point.

### `/fork <entry_id>`

Branches the session tree at a specific entry ID, creating a new branch from that point while preserving the original. The entry ID argument is required — Tau reports a missing-argument error without it. Entry IDs are shown in `/tree`.

### `/clone`

Duplicates the current branch into a new session file and switches into it. Both sessions start identical; changes in one do not affect the other. Useful for parallel explorations from a shared starting point.

### `/session`

Prints session info inline in the chat:

- **Session Info** — name (if set), file path, ID
- **Messages** — user, assistant, tool calls, tool results, total
- **Tokens** — input, output, cache read/write if any, total, with human-readable suffixes (`1.2K`, `3.4M`) and an inline USD cost estimate per line
- **Cost** — total USD cost, shown only when non-zero

### `/compact [instruction]`

Runs context compaction immediately: summarizes older messages with the LLM and replaces them with a compact summary. Pass an optional instruction to steer what the summary preserves. Useful when approaching the context limit or before a large task.

### `/copy`

Copies the text of the last assistant message on the current branch to the system clipboard.

### `/login`

If both OAuth and API-key providers are available, first asks which authentication type to use:

- **Subscription** — OAuth flow. Opens the browser and prompts for any required input inside the TUI.
- **API key** — lists API-key providers; paste the key into the secure input overlay (displayed as `***`).

Either way, credentials are saved to `~/.tau/auth.json`.

### `/logout`

Lists providers with credentials stored in `~/.tau/auth.json`. Select one to remove it. Environment variables and CLI flags are unaffected.

### `/reload`

Reloads extensions, themes, and prompt appends without restarting. Useful after editing an extension or adding a skill file. Session messages and history are unchanged.

### `/clear`

Empties the message list while staying in the same session file. Unlike `/new`, this does not create a new session.

## Message Queue

You can submit messages while the agent is still working:

| Action | Key | Delivery |
|--------|-----|----------|
| Queue a steering message | **Enter** | After the current assistant turn finishes its tool calls |
| Queue a follow-up message | **Alt+Enter** | Only once the agent goes fully idle |
| Restore queued messages to editor | **Ctrl+Up** | Immediate |
| Abort the current turn | **Escape** or **Ctrl+C** | Immediate; queued messages are restored |

On macOS, Tau displays `alt` as `option` in help text and hints.

## Sessions

Sessions save automatically to `~/.tau/sessions/`, organized by working directory.

```bash
tau                        # new session
tau --resume               # continue the most recent session
tau --resume abc123        # resume a specific session by ID
tau --fork abc123          # fork a session by ID into a new session
tau --ephemeral            # temporary session, nothing written to disk
tau --name "release audit" # set the session display name at startup
tau --session-dir ./tmp    # store sessions somewhere else
```

`--resume` and `--fork` cannot be combined. See [Sessions](sessions.md) for the file format and branching model.

## Context Files

Tau loads context files at startup to give the agent standing instructions. It looks for `AGENTS.md` or `CLAUDE.md`, matched case-insensitively, in every directory from the Git repository root through the current directory.

Outside a Git repository, Tau checks only the current directory. It loads at most one context file per directory, preferring `AGENTS.md` over `CLAUDE.md`. Files closer to the current directory take precedence over files closer to the repository root.

```markdown
# Agent Instructions

- This is a Python CLI framework project
- All code must have type hints
- Run tests before suggesting changes
```

These instructions are injected into every turn. Disable discovery entirely with `--no-context-files` (`-nc`). See [Project Context](project-context.md).

## Keyboard Shortcuts

These are Tau's default bindings. Every action can be rebound — see [Keybindings](keybindings.md).

### Messages and Agent

| Shortcut | Action |
|----------|--------|
| Enter | Submit, or steer mid-task when the agent is busy |
| Shift+Enter | Insert newline |
| Alt+Enter | Queue as follow-up |
| Ctrl+Up | Restore queued messages into editor |
| Escape | Abort the running agent; double-press runs the configured double-escape action |
| Ctrl+C | Abort the running agent; double-press within 0.5s quits |
| Ctrl+D | Quit |

### Editing

| Shortcut | Action |
|----------|--------|
| Home / Ctrl+A | Move to line start |
| End / Ctrl+E | Move to line end |
| Alt+Left / Alt+Right | Move by word |
| Ctrl+W | Delete previous word |
| Ctrl+K | Kill from cursor to end of line |
| Ctrl+U | Kill from start of line to cursor |
| Ctrl+Z / Ctrl+Y | Undo / redo |

### Navigation and Display

| Shortcut | Action |
|----------|--------|
| Page Up | Enter scroll mode (Escape or End to exit) |
| Page Down | Scroll down |
| Home / End | Scroll to top / bottom |
| Up / Ctrl+P | Move selection up in pickers and lists |
| Down / Ctrl+N | Move selection down in pickers and lists |
| Enter / Tab | Confirm selection in a picker |
| Escape | Dismiss a picker |
| Ctrl+O | Toggle expand/collapse for thinking and tool-result blocks |
| Ctrl+E | Toggle expand/collapse for template and skill invocation blocks |

> **Note:** Ctrl+E is bound both to "move to line end" inside the text editor and to the invocation toggle at the app level. The editor binding applies while typing; the app binding applies otherwise.

## Next Steps

- [CLI Reference](cli-reference.md) — flags, subcommands, and run modes
- [Keybindings](keybindings.md) — customize keyboard shortcuts
- [Settings](settings.md) — configure Tau behavior
- [Sessions](sessions.md) — session management and branching
- [Extensions](extensions.md) — add custom tools and commands
- [Skills](skills.md) — reusable on-demand capabilities
