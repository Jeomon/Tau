# Changelog

All notable changes to `tau-coding-agent` are documented here.

## 0.6.6 — 2026-07-09

### Fixed

-   Show the actual error message instead of a misleading "Found 0 results" when `web_search` fails

## 0.6.5 — 2026-07-09

### Added

-   Add GPT-5.4 and GPT-5.5 models
-   Implement strict tool argument schema enforcement and update engine state management for live tool changes
-   Add CSI-u decoding to bracketed paste to handle re-encoded control characters
-   Implement fallback model resolution for custom IDs on pinned providers, with optional thinking-level suffix support
-   Add sticky cursor column for vertical navigation in UI text entries
-   Update `google_antigravity` and Gemini APIs to support the Gemini 3 tool protocol, with comprehensive tool usage tests
-   Add thought signature persistence for the Gemini API provider

### Changed

-   Sanitize tool result content to ensure string type
-   Sanitize control characters and tabs in UI text entries
-   Terminal now defaults to bash
-   Remove deprecated and inactive models from OpenRouter provider configuration
-   Promote `distrust_thought_signatures` from `extra_params` to a dedicated `LLMOptions` field to prevent provider-side API errors
-   Extend tool call text fallback to all Gemini models lacking a thought signature

### Fixed

-   Handle tool-call IDs for Claude models in the Google API
-   Prevent session branch restore from clobbering user input
-   Address multi-turn compatibility and schema validation issues for Anthropic tool history, and thought signature state handling
-   Fix function response structure for the Gemini API provider

## 0.6.4 — 2026-07-09

### Added

-   Implement workflow engine extension for managing and running declarative multi-agent task pipelines from `.tau/workflows/*.yaml` files
-   Add schema-based structured output validation for workflow tasks
-   Implement recurring loop task scheduling extension with disk persistence and idle-gated dispatch
-   Add interactive TUI for loop management
-   Implement tool invocation renderer for consistent TUI display formatting
-   Add hackernews_filter workflow example
-   Add create-workflows skill and documentation

### Changed

-   Switch workflow execution from subprocesses to isolated in-process subagents
-   Replace subagent process spawning with in-process embedded execution
-   Allow workflow tasks to access web_search and web_fetch tools from the active session
-   Update message dequeue shortcut key binding from Alt+Up to Ctrl+Up

### Fixed

-   Ensure agent phase is set to IDLE upon loop termination
-   Allow full subagent output rendering when expanded option is enabled
-   Surface hallucinated tool calls

### Refactored

-   Remove MCP extension tests and add resolve_async stub to LLM invoke tests
-   Improve PR-review output-format rules to prevent run-on comments

## 0.6.3 — 2026-07-08

All notable changes to `tau-coding-agent` are documented here.

## 0.6.3 — 2026-07-08

### Fixed

-   `--print`/`--prompt` non-interactive mode never emitted assistant text output: it checked `hasattr(content, "text")`, but `TextContent` stores its value under `.content`, not `.text`, so the check never matched and output was always silently empty regardless of provider. Now uses `AssistantMessage.text_content()`.

## 0.6.2 — 2026-07-08

### Added

-   Add a persistent todo list tool for task tracking across sessions, with batch creation, insertion/reordering via `after_id`, fine-grained status tracking (`in_progress`/`failed`), and an above-editor task board.
-   Add a subagent extension for delegating tasks to specialized agents via markdown presets, with model inheritance from the parent session, `list`/`get` actions for discovering available agents, background task execution with full CRUD lifecycle management, and `context='fresh'|'fork'` for controlling session-history sharing.
-   Add token usage metrics to subagent steps and markdown rendering for subagent tool outputs, with configurable markdown body text styling.
-   Add sandboxed terminal execution using microVMs with automatic host fallback, plus documentation for the standalone terminal sandbox extension.
-   Add robust input validation and side-by-side UI previews to the `ask_user` extension, including support for sequential multi-question workflows.

### Fixed

-   Ensure a full UI theme refresh by clearing the frozen render cache on theme change, so switching themes now recolors previously-rendered rows instead of leaving some in the old theme.
-   Prevent session header duplication by overwriting the file on initial flush, and verify ephemeral-mode persistence logic.

### Refactored

-   Centralize numeric and byte formatting into a shared `tau.utils.format` module (used by the model picker, session stats, subagent usage line, and tool output rendering) and improve CLI error handling to surface assistant errors in print mode.
-   Simplify the subagent tool by removing persistent session/background execution in favor of defaulting to the parent model, and rename `fallback_model` to `main_model`.
-   Remove explicit agent scoping in favor of always-on project-local agent discovery with mandatory confirmation.
-   Switch `ask_user` to inline rendering and prevent voice recording during modal interaction; improve LSP tool output formatting.
-   Remove the MCP and VCC extension modules and clean up the subagent implementation accordingly.

## 0.6.1 — 2026-07-07

### Added

-   Implement a modular LLM service with dynamic provider resolution and auth management.
-   Add timeout support to hook emit and bound shutdown waits for lifecycle events and background tasks, so a hung extension handler can no longer stall exit.
-   Implement recency filtering (`day`/`week`/`month`/`year`) for web search engines, plus tool output truncation notices and untrusted-content warnings.
-   Add no-op detection and escalation to the edit tool, binary-file protection and long-line truncation to the read tool, relative-path support across file tools, and clearer terminal exit feedback (exit code/timeout/cancellation are now part of the model-facing result).
-   Update the model badge on `agent_start` so context usage reflects the current turn immediately instead of lagging until the next response.

### Performance

-   Parallelize MCP server connections on startup and pre-cache Pydantic `TypeAdapter`s used for session file parsing.
-   Deduplicate `SettingsManager` instantiation during project-trust resolution and speed up "resume most recent session" lookup, cutting redundant disk I/O on startup.
-   Optimize frozen-cell cache invalidation by partially rebuilding from the earliest modified block index.

### Fixed

-   Use a real tokenizer and an independent numeric guard for compaction overflow, with improved context estimation accuracy.
-   Clamp `max_tokens` to a minimum of 1 and update error detection patterns.
-   Force a full render when overlays are present so frozen rows no longer mask overlay updates, and invalidate stable rows when a child's frozen cache is rebuilt.
-   Align the edit tool's `content` parameter naming.
-   Restrict release artifact uploads.

### Refactored

-   Rename `tui.py` to `service.py` and update all references across the codebase.
-   Replace the `ask_user` overlay with inline component rendering and optimize message list streaming performance.
-   Remove `ast-grep` support from the grep tool and rename the edit tool parameter to `content`.
-   Support targeting existing Python interpreters in `PackageManager`.

### Documentation

-   Overhaul README and quickstart documentation with updated setup, workflow, and feature guides.

## 0.6.0 — 2026-07-06

### Added

-   Add LaTeX math rendering support to Markdown.
-   Support Gemini thinking signatures and new Google models.
-   Implement live theme preview and restoration in settings submenu.
-   Implement quiet_startup to hide session replay.
-   Add read-only `get_extensions` accessor to `ExtensionRuntime`.
-   Implement collision-resistant per-line content hashing for anchor-based file editing.
-   Implement extension priority resolution to suppress duplicate identity discovery across source locations.
-   Implement terminal tool for non-interactive shell command execution.
-   Optimize TUI rendering with incremental cell diffing to minimize terminal writes.
-   Optimize TUI rendering by reusing unchanged trailing segments during in-place line diffs.
-   Replace line-diffing with a native Buffer/Cell grid renderer.

### Fixed

-   Built-in extensions now install their declared `manifest.json` dependencies on first load.
-   Disable startup resume flag on new session and ensure history replay during quiet startup.
-   Enable toggling message details for frozen blocks by triggering cache invalidation instead of restricting access to the live tail.
-   Prevent premature freezing of streaming units by ensuring only non-final, non-streaming units are frozen.
-   Update `settings_path` resolution to handle builtin extension paths correctly.
-   Prevent markdown hyperlink rendering leaks.
-   Use provider-compatible transcription response formats for OpenAI GPT-4o,
    OpenAI Whisper, and Groq Whisper models.
-   Request and correctly parse word-level timestamps from Sarvam speech-to-text.
-   Restore CI checks.

### Refactored

-   Move and centralize Gemini tool schema transformation logic in utils.
-   Remove message list render compatibility API.
-   Make box compose native components.
-   Remove legacy component render shims.
-   Migrate extension widgets to buffer rendering.
-   Migrate tree selector to buffer rendering.
-   Standardize `Component` as an ABC, enforce `render_cells` implementation, and consolidate render testing helpers.
-   Finalize TUI migration by deprecating `render()` and enforcing Buffer-native `render_cells` across all components.
-   Migrate UI components to use the direct `render_cells` buffer-writing contract instead of returning string lists.
-   Move line hashing utility from `hashline.py` to `utils.py`.
-   Fix indentation and formatting in `WebSearchTool` class definition.
-   Improve code formatting, documentation, and logic for block expansion in message list.
-   Add explicit `finalize` method to `MessageBlock` to enable immediate freezing of completed message units.
-   Optimize rendering by freezing completed units immediately rather than keeping a fixed tail buffer.
-   Improve code readability through formatting and indentation adjustments across multiple modules.
-   Import peer types in utils and remove redundant daemon thread helpers from telemetry service.
-   Optimize Buffer memory allocation by using a shared blank `Cell` sentinel with copy-on-write semantics.
-   Clean up formatting and whitespace in LSP tool and update telemetry field examples.
-   Update interactive component selectors and add simple picker.
-   Modernize TUI component architecture with new cell-based buffer rendering system and extensive widget library.
-   Optimize TUI rendering by reusing unchanged trailing segments during in-place line diffs.
-   Stream partial terminal tool output into persistent in-place update blocks.

## 0.5.7 — 2026-07-03

### Fixed

- Built-in extensions now install their declared `manifest.json`
  dependencies on first load, same as project and global extensions. The
  `web` extension's `ddgs` / `exa-py` / `tavily-python` dependencies were
  previously never installed, since built-ins were explicitly skipped by
  the dependency-install step.

## 0.5.6 — 2026-07-03

### Added

- New `web` extension for web search and page fetching, with pluggable
  engines (DuckDuckGo, Exa, Jina, Tavily).
- New `watch` extension for retrieving video metadata and transcripts via
  `yt-dlp`.

### Changed

- Migrated telemetry from a raw `httpx` install ping to the official
  PostHog SDK, moved into its own `tau/telemetry` package, and added
  crash reporting via PostHog's exception autocapture. Both the install
  ping and the exception client run without blocking startup or delaying
  process exit on shutdown.
- Moved built-in extension and theme modules from `.tau/` into
  `tau/builtins/` for consistency with the rest of the package layout.

## 0.5.5 — 2026-07-03

### Fixed

- Stopped the TUI from enabling terminal mouse reporting. Mouse-tracking
  protocols report clicks and wheel-scroll as a single mode — there's no way
  to request "clicks only" — so requesting it for click-to-position in the
  text input was also taking over the terminal's native wheel-scroll and
  click-drag copy/select for the whole session. Native scroll and copy now
  work normally again; click-to-position in the editor is disabled until a
  way to offer it without that trade-off exists.

## 0.5.4 — 2026-07-03

### Fixed

- Redirected the `terminal` tool's spawned subprocess `stdin` to `DEVNULL`
  instead of leaving it inherited from the parent console. On Windows,
  console mode (echo/line-input) is shared per-console rather than
  per-process, so a child command that reset it (as `cmd.exe` and many
  console apps do on startup) could flip echo back on for the whole
  session — leaking raw mouse-tracking escape sequences onto the screen
  while a command was still running.
- Fixed `tau update`'s installer detection so it only upgrades via `uv` or
  `pipx` when this copy was actually installed by that tool (detected from
  `sys.prefix`), rather than falling back to whichever tool happened to be
  on `PATH`, which could invoke the wrong manager on a package it doesn't
  own and fail.

## 0.5.3 — 2026-07-02

### Performance

- Cut cold-start time roughly in half by removing several redundant or
  blocking costs from `Runtime.create()`:
  - Deferred SSL context construction in the four OAuth provider modules
    (Anthropic, GitHub Copilot, Google Antigravity, OpenAI Codex) so the
    certifi CA bundle is only loaded for a provider actually being used,
    instead of eagerly for all four on every startup.
  - Ran the git-status snapshot (used in the system prompt) concurrently
    with the rest of startup instead of blocking on it synchronously.
  - Replaced `platform.uname()`-based OS/architecture detection with
    `sys.getwindowsversion()` and `PROCESSOR_ARCHITECTURE` on Windows,
    avoiding a pair of slow WMI queries on every start.
  - Made the `tau.tui` package's re-exports lazy (PEP 562), so importing a
    single TUI submodule no longer pulls in the entire component/markdown
    framework (including `mistletoe`) for callers that don't need it.
  - Removed a redundant explicit `git.Repo.close()` call that doubled up
    with GitPython's own cleanup, eliminating extra forced `gc.collect()`
    passes on Windows.
  - Deferred pydantic schema construction for session-entry models
    (`defer_build=True`) and disabled pydantic's plugin-discovery scan
    (unused by Tau), removing a full installed-packages metadata scan from
    the first model built in the process.

## 0.5.2 — 2026-07-02

### Terminal

- Fixed console size polling for resize on Windows.
- Fixed TUI stdin pumping from a thread on Windows.
- Fixed Windows compatibility by guarding POSIX-only terminal APIs.

## 0.5.1 — 2026-07-02

### Extensions
- Added **VCC** (`examples/extensions/vcc/`) — an algorithmic, no-LLM conversation
  compactor. It hooks `before_compaction` and returns a deterministic
  `CompactionResult` built by extraction and formatting (session goal, files &
  changes, commits, outstanding context, user preferences, plus a rolling brief
  transcript), reusing tau's own cut point. Repeat compactions merge into the
  previous summary. Ships `/vcc` (compact on demand), `/vcc-recall`, and a
  `vcc_recall` tool for lossless, regex-ranked search over pre-compaction history.
  Opt-in by default (`override_default_compaction`); any failure falls back to the
  built-in summarizer.
- Reorganized the `ask_user` extension into a package directory, and open
  freeform-only `ask_user` prompts straight into the editor.
- Exposed the documented semantic colour roles on the tool-render theme so
  extension renderers can style output consistently.

### Themes
- Added a bundled set of example themes (`examples/themes/`): ayu-dark,
  catppuccin, dracula, everforest, gruvbox, horizon, and more.

### TUI
- Prevented autocomplete pickers from consuming modified keys, and added
  render-exception handling so a failing renderer can no longer freeze the UI.
- Added comprehensive lifecycle management (dispose methods) for TUI components
  and terminal state; made the TUI standalone.
- Added support for Alt+navigation sequences prefixed with an extra ESC byte.
- Corrected the `_do_render` exception-logging call.

### Terminal
- Improved terminal output streaming.
- Hardened terminal process resource management with context-manager support and
  robust `OutputAccumulator` cleanup.

### Inference
- Enabled the chat-template thinking format for diffusiongemma, with a
  validation test.

### LSP
- Resolved LSP roots to absolute paths so `as_uri()` can no longer fail.

### Refactors & Docs
- Renamed `Agent` to `Engine`, unifying terminology via `EngineContext` with
  public compatibility aliases.
- Added `docs/creating-tools.md` and inference-subsystem documentation to the
  sidebar.

## 0.5.0 — 2026-07-02

### Providers
- Added Z.ai as a built-in provider across all four modalities:
  - Text: 19 GLM chat/vision models (glm-4.6, glm-4.7, glm-5, glm-5.1, glm-5.2,
    glm-5-turbo, flash/flashx/x/air/airx variants, glm-4-32b, and the
    glm-*v vision-language models), wired through the existing OpenAI-compatible
    dialect with a `zai` thinking-format for reasoning requests.
  - Image: CogView-4 and GLM-Image via the existing `openai-image` adapter.
  - Audio: GLM-ASR-2512 transcription via the existing `openai-audio` adapter
    (Z.ai does not offer a text-to-speech endpoint).
  - Video: CogVideoX-3 and the Vidu Q1 / Vidu 2 model families via a new
    `zai-video` adapter implementing Z.ai's async submit/poll job API.
- Model IDs, context windows, output caps, thinking support, and pricing were
  individually verified against Z.ai's official API docs and pricing page;
  live API calls confirmed the image-generation and chat-completion endpoints
  behave as documented.

## 0.4.9 — 2026-07-02

### Docs
- Corrected numerous stale and fabricated claims across README.md, AGENTS.md,
  CONTRIBUTING.md, SECURITY.md, and `docs/*.md`, including: nonexistent
  providers/themes (Azure OpenAI, dracula/nord/etc.), wrong CLI flags and RPC
  command names (`bash` → `terminal`), a nonexistent `InferenceClient` class
  and `--list-models` flag, wrong Python version requirements, a fabricated
  vulnerability list in SECURITY.md, broken httpx proxy code examples (pinned
  httpx 0.28 removed the `proxies=` kwarg), a wrong `SettingItem` import path,
  fabricated test filenames and a nonexistent `Agent(client=...).run()` API
  example, and incorrect session filename/ID formats.
- Fixed the corresponding `proxies=` usage in the `get_proxies_for_client()`
  docstring in `tau/utils/http_proxy.py` to match httpx 0.28's `mounts=` API.

## 0.4.8 — 2026-07-02

### Tools
- Added `ast-grep` integration to the `grep` tool: an `ast` mode for structural,
  AST-aware pattern matching (as an alternative to ripgrep regex), and a `rule`
  parameter for `ast-grep scan` YAML rules (relational, composite, and
  kind-based queries) for structural searches a single pattern can't express.
- Added `ast-grep-cli` to the `tools` optional dependency group.

### TUI
- Implemented an `ask_user` TUI tool for interactive decision gating, with
  selector focus tracking and multi-line text editor support in the `AskUser`
  extension.
- Added configurable and idle-based cursor blinking to the terminal input
  interface.
- Implemented visual row navigation in `TextInput` for soft-wrapped text and
  preserved leading indentation when wrapping lines.
- Added a "clear" action for double-Escape, set as the default behavior.
- Enabled text pasting for prompts and disabled API key masking for
  visibility.
- Repaired extension shortcut registration with TUI conflict resolution and
  validation.
- Formalized all input, navigation, and application actions into the
  configurable KeyMap system.

### Models & providers
- Added model-specific "thinking dialect" support to standardize reasoning
  configuration and replay across diverse OpenAI-compatible providers.
- Enabled thinking support for Kimi K2.6, with reasoning field replay for Qwen
  chat templates, and enabled thinking capability for selected Mistral and
  Nemotron models.
- Updated OpenRouter reasoning request params to support the `enabled` flag
  and removed the redundant `include_reasoning` option.
- Reduced `max_output_tokens` for `openai/gpt-oss-120b` and normalized model
  pricing and max output tokens across OpenRouter models.

### Tool results & diagnostics
- Added support for `_display_content` and per-block `preview_lines` in tool
  results, and updated LSP diagnostic metadata to include preview lines and
  original display content.

### Documentation & cleanup
- Simplified inference provider documentation and updated core configuration
  and directory structure references.
- Standardized module imports, formatting, and codebase-wide code style.
- Simplified session selector logic and removed unused imports in the engine
  service.

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
