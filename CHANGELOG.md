# Changelog

All notable changes to `tau-coding-agent` are documented here.

## 0.4.7 — 2026-07-01

### TUI & model selection
- Added a voice selector component for TTS-capable models and persist voice
  selection in model management.
- Display model context windows and modality mappings in the model selector.
- Added overlay theming, form overlays, optional overlay backgrounds, dynamic
  terminal background color configuration via OSC 11, and layout spacing updates.
- Improved UI interaction styling, settings tab organization, selector navigation,
  and message truncation summaries.

### Models & extensions
- Added Claude Sonnet 5 model definitions across Anthropic, Anthropic Claude Code,
  Anthropic Vertex, and Bedrock providers.
- Added a watch extension that fetches video metadata and transcripts through
  `yt-dlp`.
- Added the `btw` extension module.

### Context & subagents
- Added ephemeral context injection for transient, non-persistent LLM context via
  hooks, the engine, and Anthropic inference.
- Implemented a subagent framework for delegated work, including agent types,
  manager, runner, task tool, creation workflow, autocomplete support, and an
  example extension migration.
- Reworked project context discovery into a hierarchical model and improved system
  prompt composition.

### Documentation
- Clarified configuration directory paths and updated web fetch tool output to
  display prompt labels.

## 0.4.6 — 2026-06-29

### Runtime & extensions
- Centralized resource discovery with `ResourceLoader` for extensions, skills,
  prompts, and themes.
- Runtime service dependency injection, in-memory inline extension factories,
  runtime SDK event subscriptions, steering APIs, and startup diagnostics for
  model fallback and stale extension contexts.
- Added `requires_idle` for commands that must run only when the agent is idle.

### Documentation
- Expanded the Python API documentation for `disable_context_files` and
  `project_trusted`.

## 0.4.0 — 2026-06-26

### Steering & follow-up reliability
- Mid-task **steering** and **follow-up** messages are now delivered reliably. The
  agent loop was restructured into a unified inner/outer loop that re-polls
  steering after every turn, so a steer that lands on a plain-text turn is injected
  and answered instead of being stranded in the queue.
- Steering/follow-up messages queued *after* the agent loop stops are drained via
  continuation turns rather than silently dropped.
- Injected steering/follow-up messages are now **persisted to the session**, so they
  survive into later turns' context and appear in the session log.
- Continuation turns are kept within the context window (auto-compaction + history
  resync), matching normal turns.
- The pending-queue UI hint clears the moment a steering/follow-up message is
  consumed (queue updates are emitted on consumption, not just on enqueue).

### Extensions
- Programmatic model switching, custom OAuth providers, and deeper tool
  introspection in the Extension API.
- Live extension toggling with clean command unregistration.
- Unified extension configuration lookup and a dynamic settings panel that refreshes
  to reflect live `settings.json` values.
- Richer extension display: manifest metadata, author attribution, and improved
  filtering in `ConfigEntry`.

### Voice input
- New voice input extension with space-hold-to-record.
- Controller lifecycle management (unload/reload), finer-grained (millisecond)
  activation hold timing, and decoupling from TUI internals.

### TUI
- Semantic UI themes in `ToolContext`; extensions now style via theme roles instead
  of internal ANSI constants.
- Interactive mode reorganized into a modular component architecture (primitives,
  overlays, modals) with consolidated utilities.
- New layout primitives: `Constrained`, `Columns`, and `Rows` with sizing utilities.
- Terminal tool output streams to the TUI line-by-line.

### Models & sessions
- Multi-modality model configuration and an availability service with updated
  provider identification.
- Unified session resumption logic with resume-command hints printed on exit.

### Fixes
- Parse the Kitty event-type sub-parameter so arrow keys work in Ghostty.
- Ignore non-dictionary extension values in the settings manager to prevent parsing
  errors.
- Resolve all ruff lint errors and pyright warnings across the codebase.

### Tooling
- Upgrade to Python 3.13; improved input parsing with adaptive release-gap handling
  for character-level auto-repeat.
