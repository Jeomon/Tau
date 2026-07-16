# Usage Guide

This page covers day-to-day usage of tau in interactive mode.

## Interactive Mode

Run `tau` to open the terminal UI. The interface has three areas:

- **Header** — current session info, model, token usage
- **Messages** — conversation history, tool calls, results
- **Editor** — where you type prompts

Named Markdown links display their label without repeating the destination URL.
In terminals with OSC 8 support, use the terminal's link gesture (commonly
Alt+click or Cmd+click) to open the destination. Autolinks remain visible as URLs.
Markdown images render as linked placeholders when inline image display is not
available; local image paths link through an absolute `file://` URI.

## Browser UI

Run `tau web` to launch the NiceGUI browser interface. It starts on
`http://127.0.0.1:8080` by default and accepts provider/model flags:

```bash
tau web
tau web --host 0.0.0.0 --port 8080 --provider openai --model gpt-5.6
```

## Editor Features

| Feature | How |
|---------|-----|
| Submit message | Press **Enter** |
| New line in editor | Press **Shift+Enter** |
| File reference | Type `@` to browse and insert file paths |
| Paste text | **Ctrl+V** |

## Slash Commands

Commands that change agent, session, model, tool, or extension state wait until
the active turn finishes. UI-only and read-only commands such as `/theme`,
`/settings`, `/session`, `/copy`, and `/help` can run while the agent is busy.
This command dispatch is separate from Enter steering and Alt+Enter follow-up
messages.

Type `/` to open the command palette. Commands are fuzzy-searchable — type a few characters to filter.

### Session

| Command | Description |
|---------|-------------|
| `/new` | Start a fresh session |
| `/resume` | Browse and resume a past session interactively |
| `/fork [entry-id]` | Branch the session tree at a specific entry |
| `/tree` | Navigate the session tree and switch to a different branch |
| `/clone` | Duplicate the current session at the current position |
| `/compact` | Summarise and compact the current context |
| `/session` | Show session info, message counts, and stats |

### Model & Appearance

| Command | Description |
|---------|-------------|
| `/model` | Pick a model by modality; Speak models with declared voices open a second voice picker |
| `/theme` | Open the theme picker |
| `/effort` | Set the thinking effort level |

### Authentication

| Command | Description |
|---------|-------------|
| `/login` | Save credentials for a provider (API key or OAuth subscription) |
| `/logout` | Remove stored credentials for a provider |

### Other

| Command | Description |
|---------|-------------|
| `/clear` | Clear all messages from the current session |
| `/copy` | Copy the last assistant message to the clipboard |
| `/reload` | Reload extensions, skills, prompts, and settings |
| `/settings` | Show current settings |
| `/extensions` | Enable or disable extensions by scope |
| `/watch <url> [question]` | Load public video metadata and captions using `yt-dlp` |
| `/help` or `/?` | List all commands and keyboard shortcuts |
| `/quit` or `/q` or `/exit` | Exit tau |

Extensions and prompts also appear in the command palette. Type `/` and browse to see everything available.

`/watch` requires `yt-dlp` on `PATH`. It reads metadata and English captions
from public URLs supported by `yt-dlp`; it does not download or inspect video
frames. When captions are unavailable, the agent receives metadata only.

---

## Command Details

### `/resume`

Opens a searchable session picker showing all past sessions for the current directory, sorted by last modified. Each row shows the session filename, timestamp, message count, and working directory. Type to filter, arrow keys to navigate, Enter to switch.

### `/tree`

Opens the session tree navigator showing every message node in the current session, indented by depth. Entry ID prefix and the first 60 characters of the message are shown. `(current)` marks the active leaf. Enter navigates to the selected branch point.

### `/fork [entry-id]`

Branches the session tree at a specific entry ID. Creates a new branch from that point while preserving the original. Useful when you want to explore a different direction from a past message. Entry IDs are shown in `/tree`.

### `/clone`

Duplicates the entire current branch into a new session file and switches into it. Both sessions start identical — changes in one do not affect the other. Useful for running parallel explorations from the same starting point.

### `/session`

Prints session info inline in the chat. Shows:

- **Session Info** — Name (if set), File path, ID
- **Messages** — User, Assistant, Tool calls, Tool results, Total
- **Tokens** — Input, Output, Cache read (if any), Cache write (if any), Total — counts use human-readable suffixes (e.g. `1.2K`, `3.4M`); each line shows an inline cost estimate in USD
- **Cost** — Total USD cost with inline estimate (only shown if non-zero)

### `/copy`

Finds the last assistant message on the current branch and copies its text content to the system clipboard. Uses `pbcopy` on macOS, `wl-copy` / `xclip` / `xsel` on Linux.

### `/login`

If both OAuth and API key providers are available, first asks which authentication type to use:

- **Subscription** — OAuth flow (GitHub Copilot, OpenAI Codex). Opens the browser automatically and prompts for any required input (device code, redirect URL) inside the TUI. Credentials are saved to `~/.tau/auth.json`.
- **API key** — Shows a list of API-key providers. Select one and paste the key into the secure input overlay (displayed as `***`). Key is saved to `~/.tau/auth.json`.

### `/logout`

Shows a list of providers that have credentials stored in `~/.tau/auth.json`. Select one to remove it. Environment variables and CLI flags are not affected.

### `/compact`

Immediately runs context compaction on the current session: summarises old messages with the LLM and replaces them with a compact summary. Useful when you are approaching the context limit or want to free up space before a large task.

### `/reload`

Reloads extensions, themes, skills, prompts, and keybindings without restarting the session. Useful after editing an extension or adding a new skill file. The session itself (messages, history) is unchanged.

### `/clear`

Clears all messages from the current session, starting fresh while staying in the same session file. Unlike `/new`, this does not create a new session — just empties the message history.

---

## Message Queue

While the agent is working you can queue new messages:

| Action | How |
|--------|-----|
| Queue a steering message | Press **Enter** (delivered after the current tool round-trip) |
| Queue a follow-up message | Press **Alt+Enter** (delivered when the agent goes fully idle) |
| Restore queued messages to editor | Press **Ctrl+Up** |
| Abort the current turn | Press **Escape** or **Ctrl+C** |

## Sessions

Sessions are saved automatically to `~/.tau/sessions/`, organised by working directory.

```bash
tau                  # new session
tau --resume         # continue most recent session
tau --resume abc123  # resume specific session by ID
tau --ephemeral      # temporary session, nothing saved
```

## Context Files

Tau loads context files at startup to give the agent standing instructions. It searches for:

1. `AGENTS.md` or `CLAUDE.md`, matched case-insensitively
2. Every directory from the Git repository root through the current directory

Outside a Git repository, Tau checks only the current directory. It loads at most
one context file per directory, preferring `AGENTS.md` over `CLAUDE.md`. Files
closer to the current directory take precedence over files closer to the repository
root.

Use context files to provide system-level instructions:

```markdown
# Agent Instructions

- This is a Python CLI framework project
- All code must have type hints
- Run tests before suggesting changes
- Follow PEP 8 style guide
```

These instructions are automatically injected into every turn, helping the agent understand your project conventions and constraints.

## File References

Type `@` in the editor to fuzzy-search for a file and insert a reference. The file's contents are prepended to your message when you submit.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Enter | Submit message |
| Shift+Enter | Insert newline |
| Alt+Enter | Queue follow-up message |
| Ctrl+Up | Restore queued messages |
| Ctrl+V | Paste |
| Ctrl+U | Clear input |
| Ctrl+W | Delete previous word |
| Ctrl+A / Home | Move to line start |
| Ctrl+E / End | Move to line end |
| Ctrl+K | Delete to end of line |
| Ctrl+C | Abort turn (double-press to quit) |
| Ctrl+D | Quit (on empty input) |
| Escape | Abort turn / dismiss picker |
| Ctrl+O | Toggle expand/collapse for thinking and tool-result blocks |
| Ctrl+E | Toggle expand/collapse for template and skill blocks |

See [Keybindings](keybindings.md) for how to customise shortcuts.

## Next Steps

- [Keybindings](keybindings.md) — Customise keyboard shortcuts
- [Settings](settings.md) — Configure tau behaviour
- [Sessions](sessions.md) — Session management
- [Extensions](extensions.md) — Add custom tools and commands
