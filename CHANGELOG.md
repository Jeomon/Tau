# Changelog

All notable changes to `tau-coding-agent` are documented here.

## Unreleased

### Changed

-   `requires-python` is now `>=3.12`, was `==3.12`. The exact pin admitted only 3.12.x, so the project could not be installed under 3.13 or 3.14 — including into the sandbox extension's own microVM, whose `python` image resolves to the latest release. Nothing in the codebase required the pin: the full suite passes unchanged on 3.14.6. `ruff`'s `target-version` and mypy's `python_version` stay at 3.12, since both should track the *oldest* supported version rather than the newest

### Fixed

-   `tau doctor` reports an extension that failed to load. It deliberately never executes extension code — a diagnostic command should not install dependencies or run arbitrary imports as a side effect — so its extension checks are static: the manifest parses, declared entry points exist. An extension that raises on import passes every one of them, which meant `/reload` could report "1 error" while `doctor` reported "no issues found", each answering a different question while appearing to answer the same one, and neither naming the extension or the reason. The loader now records the outcome of each load, keyed by extension directory, and doctor reads that record the same way it already reads the dependency-install cache. A directory with no record is not reported — an extension never loaded on this machine is not a broken one, and saying otherwise would make a fresh checkout look ill. Errors from event handlers are excluded: an extension whose `session_start` hook raised did load, and reporting it as a load failure would send someone hunting an import error that does not exist

-   Token usage is priced. `Model.calculate_cost()` existed from the start but had no production caller — only tests — so `usage.cost` kept its zero default on every message and *every* cost figure in the product silently reported nothing: the `/session` panel fell through to a hardcoded per-token estimate that had no relation to the model in use, a subagent's reported cost was always `$0.00`, and an RPC client reading `usage.cost` got zeros. The engine now prices each response from the model's own per-million rates as the token counts land. Providers report tokens, never money, so this is the only place the two meet; `usage_from_end_event()` in `tau/message/utils.py` does both steps for every code path that consumes a stream's closing event, rather than each one remembering to
-   Compaction and branch summarization no longer spend money invisibly. Both call the model, and neither recorded what that cost: `CompactionEntry` and `BranchSummaryEntry` carried no usage at all, so it was absent from every total and — unlike a message, where the usage rides along — unrecoverable afterwards. A long session that had compacted several times under-reported its own spend with no way to tell. Both entry types now carry an optional `usage`, written from the summarization call and priced like any other response. The field is `null` on entries written before this release, and on a summary an extension supplied rather than the model, since nothing was billed

-   A compaction that overlaps another one no longer wedges the agent for the rest of the session. `Agent._apply_compaction` saves the current phase in a local and restores it in its `finally`, so a second compaction entered while the first was awaiting captured `COMPACTION` as *its* previous phase and wrote that back last: `is_idle()` then answered False forever and every subsequent `invoke()` failed with "Agent is busy". Only RPC could reach it — commands are dispatched as independent tasks (`asyncio.ensure_future` per stdin line), and neither the `compact` handler nor `Agent.compact()` checked whether a turn was in flight, so a `compact` sent mid-turn raced the automatic compaction running inside that turn. The TUI was never exposed, because slash commands default to `requires_idle=True` and are deferred until `settled`. `Agent.compact()` now raises `RuntimeError` unless the agent is idle, mirroring `invoke()`, and `_apply_compaction` refuses to re-enter while a compaction is already running. The RPC `compact`, `new_session`, `switch_session`, `fork` and `clone` handlers reject mid-turn commands with a message naming `settled`, rather than letting them swap the runtime context out from under a running agent
-   Replacing a session no longer leaves the turn it interrupted writing into the session manager it was detached from. `new_session()`, `resume_session()`, `clone_session()`, `fork_session()` and `navigate_tree()` rebuilt the runtime context (or moved the branch leaf) without stopping the agent, so a turn still streaming went on appending its closing messages — and any tool results still outstanding — to a session nothing was reading any more, while the events for them reached a client already told the session had changed. All five now abort the turn and wait for it to unwind first, so the partial turn stays persisted to the session it belongs to and the swap happens on a quiet runtime. The RPC busy checks added alongside this stop a client reaching the situation at all; this closes the same hole for extensions calling `ctx.new_session()` / `ctx.fork()` / `ctx.switch_session()` and for embedders driving `Runtime` directly. The wait is skipped when an extension callback is on the stack, since that callback may itself be running inside the turn being waited for and waiting would deadlock — the abort is still requested — and it is bounded by `_SESSION_SETTLE_TIMEOUT` (10s) so a turn that never reports itself idle cannot hang the switch forever. `Agent.wait_for_idle()` alone is not enough to express "the agent is quiet": it waits on the idle event, which only `invoke()` owns, so a compaction started outside a turn (a manual `/compact`, an extension's `ctx.compact()`) moves the phase without ever clearing it and the wait returned instantly with the agent still busy. The phase is checked too, polled for the remainder of the budget since nothing signals a change to it. Modelled on the equivalent guarantee in the `pi` agent toolkit, whose `teardownCurrent()` settles the active response before every session replacement for the same reason
-   `navigate_tree()` no longer resurrects an agent phase its owner has already released. It captured whatever phase was current, set `BRANCH_SUMMARY`, and restored the captured value in its `finally` — the same pattern that wedged compaction, and wrong for the same reason: if the other operation finishes first and sets `IDLE`, the restore writes the stale busy value back over it and `is_idle()` answers False for the rest of the session. The phase is now claimed only from `IDLE` and released back to `IDLE`, so an operation that finds it taken summarizes without claiming it rather than corrupting it; the only visible cost is the "summarizing" indicator, which the current owner is already showing. Not reachable before this release — `/tree` waits for idle and no RPC command exposes navigation — so this is a latent-bug fix, not a user-visible one
-   `/reload` no longer drops a tool an extension registered after `register()` returned. `tau.register_tool` writes to the extension *object*, and a reload builds new ones by calling `register()` again — it emits `extension_reloaded` and never re-runs the handler the tool was registered from, so `replace_source` swept away anything registered from, say, a `session_start` handler, taking its `render_call` / `render_result` with it: the model silently lost a tool it had a moment earlier, with nothing in the transcript to say so. Such tools are now carried across the reload, scoped to extensions that are still loaded and to names the fresh load did not itself provide, so disabling or deleting an extension still removes its tools and a re-registered tool wins over the carried copy. The carried object is the one that was live before the reload and may close over state torn down in `extension_unload`; an extension that owns such state should re-register from `extension_reloaded`, which replaces the carried copy by name

### Added

-   `rlm_query` answers questions about text far too large to read, implementing Recursive Language Models ([arXiv:2512.24601](https://arxiv.org/abs/2512.24601)). Reading a huge log or a whole directory the ordinary way costs the entire conversation rather than one turn: once the text is in the transcript it is resent on every later turn, and the model degrades as its context fills with material it has finished with. The tool inverts that - the text is loaded into a Python REPL as a variable, and a model narrows it down with code (slice, regex, count, chunk) before calling `llm_query()` on the pieces that need judgement a regex cannot make. Only the answer returns, so what lands in the transcript is a few hundred characters whether the input was ten kilobytes or ten megabytes. Recursion is capped at depth one, matching the paper's own experiments, and a sub-call budget bounds the cost, which those calls dominate. Inputs under 2000 characters are refused with a pointer to `read`: several model calls to fetch what one read would return is a worse answer, not a better one. The REPL exposes an explicit namespace - text and counting modules, no file or network access - which is a legibility boundary rather than a sandbox, since model-written Python runs in-process and the `terminal` tool already runs arbitrary commands. Ships as a bundled example extension in `examples/extensions/rlm`, so it is self-contained: its modules import each other relatively rather than reaching back into the `tau` package, which is what lets a copy in `.tau/extensions/` load against an installed tau that has never heard of it. Turn it off or retune its budgets under the RLM extension settings
-   `session_storage: "sqlite"` selects the SQLite session backend. `SQLiteSessionStorage` has been complete, documented and conformance-tested since it was written, and no user could reach it: `SessionManager` constructed only the in-memory or file backends, and no setting selected otherwise. It was a tested implementation with no caller. `SessionManager` now takes a backend and binds it through the existing `session_file` seam, writing a project's sessions into one indexed `sessions.db` rather than a JSONL file each, and listing reads *both* backends so a project that switched still shows its whole history. The measured payoff is listing: deriving a session's name and message count means reading its history, so `/resume` parses every line of every session file (182 ms for 35 MiB), where the same information is one indexed `GROUP BY` (29 ms) that scales with session count rather than bytes on disk. The default stays `"file"` — this changes nothing unless asked for. The `/resume` picker was taught to identify a session by id rather than by path, since under SQLite every session of a project shares one: it commits the selected session instead of its location, and hiding the active session, highlighting a delete target and refusing to delete the session you are in all compare ids. That last change also fixed a destructive bug the backend would have exposed — deleting a session called `path.unlink()`, which for a database would have destroyed every session in the project; a SQLite session now has just its own rows dropped. `tau --fork` stays file-only, because `fork_from()` writes JSONL directly without a backend
-   `/search <text>` finds a past session by what was said in it. `/resume` lists sessions by name and date, which only helps when you remember one of those; what you actually remember is the content — "the session where we fixed the compaction race" — and nothing could answer that, so a session you could not name was effectively lost. Matching is a case-insensitive substring test over user and assistant message text plus compaction and branch-summary prose, which are often the most memorable thing in a long session. It needs no index, no schema change and no query syntax; the cost is kept down by testing the raw line before parsing it, so a non-matching entry costs a substring scan rather than a JSON parse. Results open the ordinary `/resume` picker with its list pre-filtered to the sessions that matched, so choosing one resumes exactly as `/resume` would rather than through a second, subtly different path. Modelled on `harness/session/search.ts` in the `pi` agent toolkit
-   `/trust` shows and changes whether the current project is trusted. Trust decides whether Tau loads a project's own `.tau/` settings, extensions and context files, and the machinery for it was complete but reachable only from the startup prompt: a decision, once made, could not be inspected, and reversing it meant editing `~/.tau/trust.json` by hand. Declining trust on a project you later wanted to work in was effectively permanent. Bare `/trust` reports the decision as its two independent halves — what is in effect for this process and what is stored on disk for next time — because a session-only answer makes those differ on purpose, and an inherited decision names the parent directory that carries it. `/trust yes` trusts and remembers, `session` trusts without writing anything to disk, `no` records a refusal, and `forget` drops the stored answer so the next start asks again while leaving the current session as it is. Granting trust reloads extensions, since project settings were skipped at startup and context files are read while the session is built

-   `/session` and RPC's `get_session_stats` report prompt-cache waste: the tokens re-billed at full price because the cache missed, what that cost, and why. Every turn resends the whole conversation, and providers that support prompt caching bill an unchanged prefix at a fraction of the input rate — Anthropic reads at a tenth — so the cache is what keeps a long session's cost flat rather than quadratic. When it misses nothing says so: the turn looks normal and the bill is simply larger. The two ordinary causes are both invisible at the time, and both are now attributed — idling past the cache TTL (five minutes on Anthropic) and switching model, which starts a fresh cache. Misses at or below 1024 tokens are ignored as breakpoint granularity rather than a real re-bill, a provider that never reports cache activity is never accused of missing, and compaction or a branch summary resets the comparison because the prompt after one is new content rather than re-billed content. Modelled on `cache-stats.ts` in the `pi` agent toolkit

-   RPC `get_available_thinking_levels` returns the levels the active model supports and the one currently set. `cycle_thinking_level` already walked exactly this set but never reported its contents, so a client could step through them blind but not render a picker. A model that advertises no levels reports every level — absent metadata means unknown, not unsupported, matching how cycling already treats it

-   RPC `message_update` now carries `delta` and `thinking_delta` — the text appended since the previous tick of the same message — alongside the full `message` it always sent. The full message was the only shape available, so a client wanting just the new characters still paid for the whole accumulated reply on every token: the stream grows with the square of the reply length, measured at 38 MB of traffic for a 39 KB answer. The new `set_update_mode` command takes `"full"` (the default, unchanged wire shape) or `"delta"`, which omits the redundant `message` copy and takes that same reply to 0.11 MB — 341x less. `message_end` still carries the complete message in both modes, so nothing is lost. A delta is measured against the previous tick and reset by `message_start`; when a block is rewritten rather than extended (a `TextEndEvent` replaces a streaming block outright) the delta carries the whole new text. `tau -p --json` already emitted deltas for exactly this reason; RPC did not
-   `tau -p --mode json` takes a new `--json-events` flag: `compact` (default) or `full`, the latter being everything RPC mode sends. JSON and RPC are the same stdout protocol — one emits events, the other adds commands on stdin — but each maintained its own hand-written list, and the JSON one had drifted to 9 of the 22. Rather than widen it for everyone and change what existing consumers see, the extra thirteen are opt-in. One event is not: `message_rollback` joins the default set, because omitting it is a correctness bug rather than a verbosity choice — an interrupted tool turn persists an assistant tool-call message and its result before the abort lands, both are then withdrawn, and a consumer that never hears about the withdrawal silently diverges from the session file. So a consumer written before this release sees exactly one new event type. JSON mode also picks up three protections RPC already had and it lacked: a stray `print` from a tool or extension can no longer corrupt the stream (fd 1 is duplicated for the protocol and pointed at stderr), a slow consumer applies backpressure instead of the write blocking the event loop, and a value that is not JSON-native is coerced rather than taking its whole event off the stream — a tool returning an image used to raise inside the hook, which `Hooks.emit` logs and swallows, so the result simply never arrived
-   RPC mode reports a conventional exit code when signalled: `143` for `SIGTERM`, `129` for `SIGHUP`, where both previously exited `0`. A supervisor could not tell a killed server from a client that closed stdin — both looked like success. The stream is flushed and stdout restored before the code is reported, so a signalled server still delivers what it had buffered. `SIGINT` stays `0`, since interrupting a headless server at a terminal is a normal way to end a session. The rule now lives in `tau/modes/signals.py` and is used by `rpc`, `print` and `json` alike, rather than each mode deciding for itself
-   The protocol's unbuffered fallback write path retries a full pipe instead of losing the line. It is used before the async writer is attached (the `ready` line, early extension errors) and wherever a pipe transport is unavailable, and has no flow control underneath it — so a reader that has not drained yet makes the fd report `EAGAIN`/`EWOULDBLOCK`, or `ENOBUFS` on BSD and macOS, and the write simply vanished mid-stream. Retries are bounded at 5s, after which the line is dropped with a warning rather than stalling the agent on a client that has stopped reading; `EINTR` resumes rather than counting against the budget
-   `--prompt`/`-p` can be repeated: each prompt runs in turn against the same session, so a later one sees everything the earlier ones did, and each waits for the previous to settle rather than landing mid-turn. Piped stdin and `@file` contents still attach to the first prompt only. Applies to `json` mode too, which emits one continuous event stream across the whole sequence
-   Print and json modes handle `SIGTERM` and `SIGHUP`. A single-shot run is usually driven by a script or a CI step, so it tends to be killed rather than quit — and with no handler the agent kept streaming after the shell had moved on, with whatever its tools had spawned left orphaned. The running turn is now aborted, which is also what stops in-flight tools, and the session is written out before Tau exits `143` or `129` respectively. `SIGINT` is deliberately untouched: Python's `KeyboardInterrupt` already unwinds the run, and intercepting it would swallow Ctrl-C

### Refactor

-   Session counts and spend are computed once, in `tau/session/stats.py`. The `/session` panel and RPC's `get_session_stats` each walked the branch themselves and had drifted: the panel reported tool calls, tokens and cost while RPC returned message counts only, so a client wanting the rest had to pull every entry with `get_entries` and re-derive it — including the rule that cache tokens are only added for providers that report them separately from `input_tokens`, which it had no way to know about. `get_session_stats` now returns `toolCalls`, `toolResults`, `summaries` and a full `usage` block alongside the counts it already had

-   Print and json modes moved out of `console/cli.py` into `tau/modes/print/mode.py`, which was an empty namespace package while both run modes lived in the CLI — contradicting the layout `docs/project-structure.md` describes. They are one function now (`run_print_mode`) rather than two, since they differ only in what reaches stdout; the prompt sequence, settle-waiting and signal handling are written once. `console/cli.py` builds the message list and calls it, and is ~110 lines lighter
-   The outgoing side of both stdout protocols moved into `tau/modes/wire.py`: `ProtocolOutput` (stdout guard + backpressure), `serialize_event` / `json_default`, `StreamDeltas`, and the single `FORWARDED_EVENTS` list. `modes/rpc/mode.py` and `console/cli.py`'s `_run_json` each carried their own copy of all four, which is why they had drifted in both directions — the JSON mode had delta output RPC lacked, RPC had un-encodable-field handling, a stdout guard and backpressure the JSON mode lacked. Neither difference was deliberate; both were fixes applied in one place and never propagated. About 10 KB of duplicated code is gone from `modes/rpc/mode.py` and the JSON mode's private `_serialize` / `_update_payload` / `_appended` with it. RPC's wire output is unchanged; a test asserts both modes reference the shared objects rather than growing private copies again. Modelled on the `pi` agent toolkit, whose `toJsonEvent()` is called by both its print and RPC modes for the same reason

## 0.9.2 — 2026-08-06

### Breaking

-   The xAI OAuth provider id is now `xai-supergrok`, was `xai-grok`. The id is the `auth.json` key, the `--provider` value and the `tau auth login` / `tau auth logout` argument, so an existing SuperGrok credential stored under `xai-grok` is no longer found and the provider reads as logged out. Run `/login` again, or rename the key in `~/.tau/auth.json`. `/logout` still lists the stale entry (under its raw id) if you want to clear it. Settings, scripts and session files naming `xai-grok` as the provider need updating too. The `grok-4.5`, `grok-4.3` and `grok-build` model ids are unchanged

### Added

-   ` ```mermaid ` (and ` ```mmd `) fences render as Unicode box art instead of printing their source. Flowcharts, state, sequence, class, ER, pie, gantt and mindmap diagrams all lay out. This uses `termaid`, a new dependency: it parses Mermaid source directly and draws with box-drawing characters, so diagrams appear in **every** terminal rather than only those speaking an image protocol, and it needs no Node, browser or network. The alternatives were rejected for good reasons — `mermaid-py` POSTs the diagram source to the third-party mermaid.ink service by default, `mermaid-cli` needs a Node toolchain and renders images only kitty/iTerm2 can show, and `mermaidx` drags in a JS engine and an SVG rasteriser for a PNG path measured at 6.9 seconds. Rendering takes single-digit milliseconds, so it runs inline, and output is cached by source because a scrollback repaint re-renders every frame. Three cases fall back to the previous fenced-code rendering so the source is always visible: a diagram that cannot be parsed, one wider than the terminal (`termaid` lays out to whatever width it needs, with no way to constrain it), and a fence that is still streaming in, since a half-written diagram relayouts on every token

-   `tau/session/storage.py` introduces `SessionStorage`, a pluggable backend for one session's durable history, with three implementations: `FileSessionStorage` (the existing JSONL format), `InMemorySessionStorage` (nothing on disk) and `SQLiteSessionStorage` (one `.db` per project, holding all its sessions — implemented and tested, but not reachable in a running tau: session *discovery* is still path-based, so nothing constructs it yet). It follows the shape `tau/auth/storage.py` already uses for credentials. A backend owns encoding only; branching, compaction, shedding policy and merge rules stay in `SessionManager`, which remains the sole decider of *what* gets written. `SessionManager` now performs all of its own I/O through the seam: locking, reading, appending, rewriting and selective rehydration, with `session_file` becoming a property whose setter rebinds the backend so the path and the storage object can never disagree. The `.bak` copy that guards a rewrite against destroying unparseable lines moved into `FileSessionStorage` as an overridable hook, since it describes the JSONL format rather than session semantics. The `open()`, `fork_from()` and listing entry points still address files directly: those are repository concerns — finding, naming and copying sessions — not encoding, and `fork_from` additionally serialises without `exclude_none`, so routing it through the seam would change the bytes it writes. Behaviour is unchanged, verified by replaying one deterministic workload (flush-on-first-assistant, mixed entry kinds, undo, compaction, resume, navigation, branched sessions, in-memory, read-only, the trust flow, forking, empty-file rejection and unparseable-line backup) against both implementations and diffing the result: 235 normalised lines identical, and the four write paths proved byte-identical against the previous inline expressions. One conformance suite runs against all three backends, which is what caught the four ways they were initially not substitutable: `FileLock` raises `RuntimeError: Deadlock` when a second instance for the same path is acquired in one thread, so a fresh lock per call broke the moment `rewrite()` was called inside a caller's `lock()` block, and the file lock is now taken once at depth zero behind a reentrant wrapper; an emptied session file still satisfied `Path.exists()` and reported durable state the other backends no longer had; the in-memory backend skipped the "history not starting with a session header is not a session" rule the file reader applies; and it returned its live list, letting a caller's mutation reach into stored history. Order is part of the contract, since `_build_index` resolves the current leaf by taking the last entry it sees. The SQLite backend keeps one database per **project** — the directory tau already maintains per project, collapsed into a single file whose rows are scoped by `session_id`. The alternative, a database per session, would have left listing exactly as slow as it is today: `build_session_info` derives a session's name and message count by reading its history, so `/resume` parses every line of every session file and costs O(total bytes on disk). `list_sqlite_sessions()` answers the same question with an indexed `GROUP BY`, deserializing only header and name rows — 47ms drops to 7ms at 8.7 MiB, 182ms to 29ms at 34.9 MiB, with identical message counts. Entry ids are unique per session rather than globally, since a fork legitimately copies an entry id into a second session. Projects remain independent, with no cross-project write contention. The database runs in WAL mode, so a `-wal` and `-shm` file sit beside it while a session is open and are removed on close. Reads never create a database, matching the file backend: probing a project that has none must not leave an empty one behind

### Changed

-   LaTeX math conversion moved out of `tau/tui/markdown.py` into `tau/tui/latex.py`, alongside the new `mermaid.py`, leaving the Markdown renderer to render Markdown. The module exposes `extract_math()` and `restore_math()`; the private-use placeholder that survives mistletoe tokenization no longer crosses the module boundary, so `markdown.py` carries the returned replacement list and hands it straight back rather than knowing the marker's shape. Behaviour is unchanged — verified byte-identical against the previous implementation across 14 math, code-span, table and currency cases at two widths
-   The `/login` and `/logout` pickers say what each step is asking for. `OAuthSelector` built its heading from `mode` alone, so all three `/login` screens read "Configure provider:" — including the first, which asks for an authentication method, not a provider. It now takes an optional `title`, falling back to the old mode-derived wording, and each step supplies its own: "Select authentication method:" (rows "Sign in with an account" and "Sign in with an API key", replacing "Subscription" and "API key"), "Select an account to sign in with:", "Select a provider for the API key:", and "Select an account to sign out of:"
-   Escape inside `/login` steps back one screen instead of abandoning the flow. Every screen passed the same "Login cancelled." handler, so picking the wrong authentication method — or reaching the key prompt and wanting a different provider — meant restarting from `/login`. The provider steps now return to the method picker and the key prompt returns to the provider list, matching `/settings`, where Escape leaves a submenu and only closes the panel from the top level. Escape still cancels from the first screen, including the case where only one authentication style is available and the method step is skipped, which makes the provider list the first screen
-   Every OAuth provider display name now reads `<Vendor> <Product> (Subscription)`, so the `/login` account list is uniform: `OpenAI Codex (Subscription)`, `Anthropic Claude (Subscription)`, `GitHub Copilot (Subscription)`, `Google Antigravity (Subscription)`, `xAI SuperGrok (Subscription)`. They previously mixed four shapes — a plain name, a parenthetical tier, and the word "Subscription" buried inside a parenthetical. The tier details these names dropped ("Claude Pro/Max", "ChatGPT Plus/Pro") remain in the Requires column of the OAuth table in `docs/inference-providers.md`. These names are display-only — the provider registry keys on `provider.id` — so ids, `auth.json` keys and `tau auth login <id>` arguments are all unaffected

### Fixed

-   Provider errors show the server's own sentence instead of a raw response payload. A rate-limited xAI request rendered as ``Rate Limit Error: Error code: 429 - {'code': 'subscription:free-usage-exhausted', 'error': "You've used all the included free usage..."}`` — the Python dict repr the extraction added in 0690043 was meant to prevent. That extraction only understood a mapping on the exception's ``.body``, but the openai SDK unwraps the payload before attaching it (`data = body.get("error", body) if is_mapping(body) else body`), so a provider whose payload is `{"code": ..., "error": "..."}` leaves ``.body`` a bare string and the check fell through. Reproduced against a local server returning the real 429 through the streaming call the Responses adapter makes, which also ruled out the streaming wrapper as the cause. Auditing the other SDKs found three more shapes with the same symptom: mistral attaches the undecoded JSON *document* as a string, google-genai attaches no body at all and parses the payload onto ``.message``, and the Antigravity adapter built its own error as ``f"HTTP {code}: {raw_body}"``. All four are handled now, and the image, video and audio layers — which held a response body rather than an exception and interpolated ``response.text`` directly — go through the same rules via the new `format_error_body()`. `format_http_error_body()` in the OAuth utils delegates to it so the two sets of rules cannot drift apart. A payload with no recognisable message field is still shown, collapsed to a single line rather than hidden, since losing the detail entirely would be worse. `format_exception_message()` had no tests at all, which is why this survived; it and `format_error_body()` now have 23
-   `SQLiteSessionStorage` no longer fails with `database is locked` when several sessions of one project open it at once. Switching journal mode needs a brief exclusive lock and SQLite does *not* run the busy handler for it, so a connect storm on a fresh database raised immediately however long the busy timeout was — which the per-project layout made reachable, since sessions of one project now share a file. The journal mode is a persistent property of the file, so losing that race is harmless: whoever won has already set it for everyone. WAL is now attempted only when the file is not already in it, retried briefly, and falls through to the existing mode rather than raising, since correctness never depended on WAL — only read/write concurrency does. The busy timeout is set as the first statement on the connection so the schema DDL is covered too. Found by an intermittent failure under randomised test ordering, then reproduced deterministically at 8 concurrent writers
-   Listing sessions is ~1.9x faster, and `build_session_info`'s docstring no longer describes an optimization the code does not implement. It claimed "only the first line (header) and first ~30 lines (for the name) are read... so we never deserialize the whole conversation history just to list sessions", but the loop has no early exit: `message_count` is a count of message entries, and only the last line proves there are no more. Every line was therefore parsed with `json.loads` on every `/resume`, which profiling showed to be the dominant cost — 5.9ms of a 7.7ms call on a 1.7 MiB session, against 0.5ms of file I/O. Lines are now parsed with pydantic-core's Rust JSON parser, already used by `_cheap_parse_lines` for exactly this reason and already a transitive dependency, and the docstring describes what the function really does: it visits every line but builds exactly one model, the header. Output is unchanged, verified by differential comparison against the previous implementation across 42 files including named, unnamed, blank-line, corrupt-line, empty and headerless sessions. The tempting further shortcut — counting lines that contain the bytes `"type":"message"` instead of parsing them — is now forbidden by a test: an extension's custom data is embedded as real nested JSON rather than an escaped string, so those bytes legitimately appear on entries that are not messages
-   The iTerm2 inline-image sequence now carries `size=`, the decoded byte count. iTerm2 itself treats the argument as optional — it only drives the progress indicator — but the xterm.js image addon requires it and rejects a sequence without one, so images silently failed to render in web terminals and anything else embedding xterm.js. The count is derived arithmetically from the base64 payload actually being sent, rather than from the original bytes (a non-PNG image may have been re-encoded first) and without decoding a payload that can be megabytes
-   A session interrupted mid-tool-call can be resumed again. The assistant's tool-call message is persisted before the tools run and the results after, so a crash or `kill -9` anywhere in between — a window as long as the tools take — left a call nothing ever answered. Every provider rejects that (`tool_use ids were found without tool_result blocks`, `must be followed by tool messages`), the error classifies as `FORMAT_ERROR` and is therefore not retried, and each resume resent the same malformed history: the session was wedged for good. `to_llm_messages` now gives any unanswered call a synthetic error result, which is truthful — the tool really did not finish — and matches what the engine records for an interrupted tool. Only the outgoing projection is repaired; the JSONL still shows that no result was produced. `drop_orphan_function_call_outputs` already covered the mirror case, a result whose call a compaction had folded away
-   Truncating a line through an OSC 8 hyperlink no longer leaves the link open. `truncate_to_width`, `truncate`, `clip_to_width` and `slice_columns` all preserved the opening sequence and dropped the closing one, so the terminal went on treating everything printed afterwards as part of that link — the ellipsis, the rest of the row, and whatever the next line painted. `truncate` looked safe because it appends an SGR reset, but a reset does not end a hyperlink; only the OSC 8 terminator does. Each now closes an open link at the cut, `truncate` placing the close before its ellipsis, and `slice_columns` closing a window that opened inside a link because it re-emits the opening sequence with the rest of the active SGR. Text that is not truncated is returned byte-identical, and no close is added when the cut falls after the link already ended
-   Streaming replies no longer flash raw Markdown syntax before it renders. An inline construct is literal text until its closing delimiter arrives, so the incremental renderer was faithfully drawing `[Tau docs](https://exa` — for the 36 characters it took the URL to stream — before it snapped to a link, and the same for `**bold`, `*italic*`, `` `code` ``, `~~strike~~` and `![image](…)`. `StreamingMarkdownRenderer` now trims the live tail at the first unresolved construct, so the phrase appears once, already styled. The hold is bounded to the tail's last line: a delimiter the model never closes flushes as soon as the newline arrives, and a delimiter that is literal text (`~100ms`, `4 * 5`, a `[1]` citation) is either not held at all or resolves at end of line. Fenced code blocks are exempt, since every delimiter inside one is literal
-   A project extension configured through `/settings` no longer reappears in the `/extensions` panel as a second, separate entry under "Global". Every manifest-driven setting was persisted to global settings regardless of where its extension came from, so the first value set on a project extension minted a stray global record — keyed by the project-relative path the loader had computed for it, which names nothing at all from any other working directory. The panel reads an extension's scope from the list it was found in, so that record rendered as an independent copy of the extension with its own enable switch, free to contradict the real one. `set_extension_config_key` now takes the scope it is writing for, and the loader passes the extension's actual source; project config lands in the project's `settings.json`, everything else stays global with an absolute path
-   An extension recorded in both `settings.json` scopes is no longer counted twice when settings are merged for loading. The two scopes deliberately spell the same directory differently — project entries relative to the project root, global ones absolute — but the merge was keyed on the raw string, so one extension could occupy two slots. Because a disabled entry acts as a veto by name during discovery, a stale global `"enabled": false` row could silently override a project's `"enabled": true` and keep an extension the project explicitly asked for from ever loading. Entries are now keyed by resolved path, with project winning, matching the loader's existing project > global priority
-   The `/extensions` panel shows one row per extension where both scopes hold a record for it, resolving the collision the same way the loader does, so configurations already carrying a stray entry read correctly without hand-editing the file. Its rows are also grouped by scope again — the scope heading is emitted whenever it changes between adjacent rows, and discovered extensions were appended in whatever order they loaded, so a global one following a project one could print a second "Global" heading partway down the list
-   A branch summary that the model cut off at its own output token limit is no longer persisted as if it were complete. `generate_branch_summary` read the accumulated text and only rejected it when empty, so a `stop_reason=length` response was stored verbatim as a `branch_summary` entry — truncated mid-sentence, silently missing everything past the cutoff, and injected into the destination branch's context as the authoritative record of the path the user left. Compaction has guarded against exactly this since it was written; branch summarization now applies the same check and reports the failure through the existing path, so navigation completes without a summary rather than with a corrupted one
-   PageUp, PageDown, Home and End now move the cursor in every picker. `SelectList` implemented all four and dispatched them through the `tui.select.*` keymap, but nothing ever called its `handle_input`: the inline pickers drive the widget by method call from `SelectorController`, the modal picker does the same, and the theme, effort, voice, extension, OAuth, `/settings` and `ctx.ui.multi_select()` pickers are separate widgets that only ever handled up and down. So the four documented bindings reached no code at all, and a long model or session list could only be crossed one row at a time. The widget's own unit tests passed throughout — they call `handle_input` directly, which nothing in the app does. Page jumps clamp at the ends rather than wrapping the way the arrow keys do, and the theme picker's live preview fires on a page jump as it does on an arrow
-   Closing `/settings` without editing anything now reports "Settings closed — no changes." instead of "Settings saved.". The close handler announced a save unconditionally, so opening the panel to check a value and pressing Escape claimed a write that never happened. The panel compares the merged settings view captured when it opened against the one in force when it closes, rather than the manager's modified-field sets, which are sticky for the life of the process — a field edited in an earlier visit is already marked, so a genuine second edit to it would have read as no change. Comparing values also means re-selecting the option already in force is correctly not a change, while extension sub-panel edits, which bypass the panel's own `on_change`, still are
-   Picker footers name the page keys they accept: `↑/↓ PgUp/PgDn Home/End move  ·  Enter select  ·  Esc cancel`, and the `ctx.ui.multi_select()` equivalent with its `Space toggle`. The hints still read `↑/↓ to move` after PageUp/PageDown/Home/End started working, so `/login`, `/logout`, `/theme`, `/effort`, the voice picker and the `/extensions` panel all advertised less than they accept. The three navigation keys share one clause because a separator each pushed the line past 80 columns, where the picker wraps it onto a second row

## 0.9.1 — 2026-08-03

### Breaking

-   JSON mode's `message_update` now carries `delta` (and `thinking_delta`) — the text appended since the previous update — instead of `message`, the whole reply so far. The old payload was re-serialized on every streamed token, so stdout grew with the square of the reply length: a 188 KB answer emitted roughly 1.5 GB, and large writes could exhaust memory before the turn finished. The same reply now emits about 1 MB. Consumers that read `update["message"]` should concatenate `delta` instead, or read the finished message from `message_end`, which is unchanged

### Added

-   `TAU_OAUTH_CALLBACK_HOST` overrides the address every OAuth callback server binds, as one host or a comma-separated list. The default is loopback on both stacks, which is right for a desktop login and keeps the port off the local network; it is wrong when Tau runs in a container or VM and the browser is on the host, because Docker's `-p` forwarding can only reach a process bound to the container's external interface. Resolved when the server starts rather than at import, so setting it on the command line works
-   `kimi-k3:cloud` in the Ollama Cloud catalog, with the 1,048,576-token context window and vision capability the daemon reports. Cloud-linked tags are excluded from local Ollama discovery because they duplicate this list, so they are only selectable once catalogued

### Fixed

-   `glob` returned no matches for any pattern containing a `/` — including the `src/**/*.py` form its own schema advertises. ripgrep matches such a glob against the whole path it walks, so an absolute search root meant the pattern was tested against `/abs/base/src/…` and never matched; only basename patterns like `*.py` worked. The walk is now rooted at the base directory, and results are still returned as absolute paths
-   `grep`'s `include` filter had the same flaw, so scoping a search with `include="src/**/*.py"` silently matched nothing
-   `grep`'s `files_searched` count is no longer collapsed to a single entry on Windows, where splitting an absolute path on `:` yielded the drive letter rather than the file
-   The `ask_user` dialog stayed on screen when the engine abandoned the call — its `tool_timeout_seconds` firing, or the turn being aborted — leaving the question mounted over the input area while the agent had already moved on, so the TUI never returned to the prompt. The teardown existed but sat below the `await` that actually parks for the whole question, so cancellation skipped it
-   Images returned by tools are now resized to fit 2000×2000 before entering the context window, honouring the existing `image.auto_resize` setting. Only pasted images were processed before, so `read`, browser and desktop screenshots, and extension tools sent images at full resolution. Providers validate every image in a request, not just the newest — Anthropic drops its per-image cap from 8000px to 2000px once a request carries many images — so one oversized capture could start failing every later request, and because it is written to the transcript it survived reload and wedged the session for good. A `[Image: original WxH, displayed at …]` note records the scale factor so coordinates still map back
-   A tool result carrying both an image and more than 50 KB of text no longer loses the image: truncating the text rebuilt the result without its media, dropping the screenshot a browser or desktop tool returned alongside a large page dump
-   Multi-line paste no longer submits on its first line in terminals without bracketed paste (Termux, some tmux setups), where a paste arrives as plain bytes and its embedded carriage returns were indistinguishable from Enter. A CR with more bytes behind it in the same read is now treated as a newline, since a real Enter arrives alone; the heuristic switches off permanently once a bracketed paste proves the terminal wraps them. No keystroke timing is involved, so Enter keeps its existing zero-latency path everywhere
-   Switching models no longer strands a session that already contains media the new model cannot accept. `read` refuses to load an image on a model that cannot see it, but media accepted earlier stayed in the transcript, so every later request still carried it and the provider rejected the whole payload — failing again each turn until the session was started over. A wrong `input` entry in a model catalog caused the same loop with no switching at all. Image, audio, video and file blocks are each replaced with a short note on the way out when the active model does not list that modality; the stored transcript is untouched, so switching back to a capable model restores them
-   A byte-order mark in `auth.json`, `settings.json` or the model catalog no longer discards the file. `json.loads` rejects a leading BOM outright, so a credential store carrying one parsed as empty — every stored login appeared to vanish and new ones could not be saved — while settings and catalog overrides were silently ignored as if corrupt. Windows editors and PowerShell's `>` redirect add a BOM by default, so hand-editing these files was enough to trigger it. Reads now use `utf-8-sig`, which strips a mark when present and is a no-op otherwise; writes stay plain `utf-8`, so Tau never introduces one
-   `grep` results are no longer displayed with altered text. The renderer split each `path:lineno:text` line on the first colon and then on the first `": "`, but matched code is full of `key: value` — so a match on `{"a": 1}` was shown as `{"a"  1}`, text the file does not contain. Lines without a `": "` fared no better: the split silently collapsed, dimming the whole row instead of just the location. The line number is now the anchor, which also keeps a Windows `C:\` path intact, and the truncation notice — dropped entirely by the old colon filter — is shown again with the cap it names
-   `ctx.send_user_message(..., trigger_turn=True)` now runs `/` commands and `!` shell input instead of sending them to the model as literal text. It is documented as starting a turn "as a normal user message", but it called `invoke()`, which skips the dispatch every typed message goes through — so an extension asking for `/compact` just told the model about a slash. The queueing paths (`steer`, `follow_up`) are unchanged and still deliver plain text: they carry messages to the model mid-turn, where a command has nothing to act on
-   OAuth callback servers now listen on both loopback stacks. The redirect URI is registered with each provider as the `localhost` name, which resolves to `::1` on IPv6-first systems and `127.0.0.1` elsewhere, so binding one stack left the browser's callback refused on the other and the login hanging until it timed out. Google Antigravity bound every interface instead, which avoided that but exposed the callback port to the local network for the duration of the login; it is now loopback-only like the rest
-   A busy callback port no longer aborts an Anthropic Claude Code or Google Antigravity login. Both called the callback server unguarded, so a port already in use raised straight out of the login, even though both already race the browser callback against pasting the redirect URL by hand and `on_manual_code_input` is documented as the fallback for exactly this — the path was simply unreachable. OpenAI Codex already fell back to its device-code flow. When neither a callback server nor a paste path is available the failure is now immediate and explains itself, rather than waiting out the five-minute timeout on a code that cannot arrive
-   Kimi K3's reasoning effort is no longer pinned to `max`. The catalog exposed only that level and the Moonshot dialect discarded every other selection, but the model accepts `low`, `high` and `max`, defaulting to `max` when the field is omitted. All three are now selectable and the full `ThinkingLevel` range maps onto them; reasoning cannot be disabled on K3, so an `off` selection sends no field and leaves the model's own default in place

## 0.9.0 — 2026-07-26

### Breaking

-   `edit` now requires the file to have been read in the same session. An anchor is only meaningful against the read that produced it, and the retained content digests are what let a collision be detected rather than avoided; without them a narrow token has no defence, so a missing record is refused with a re-read instruction rather than resolved on a guess
-   Anchors that resolve to two content-identical lines are refused unless the surrounding context identifies which one was read. The previous behaviour picked whichever copy sat nearer the anchor's line number, which silently edited the wrong line whenever the file had shifted
-   Writing to a symlink now updates the file it points at instead of replacing the link. Any workflow relying on a write to *replace* a symlink with a regular file will see the target rewritten instead

### Added

-   Anchor collision detection: `read` retains a two-character content digest per line and `edit` verifies the line an anchor resolved to against it, catching the case where a token now names different content
-   Content-identical lines are separated by the neighbourhood `read` displayed — an unbroken run of agreement counted outward from the anchor — so a duplicated line stays addressable after the file moves, and is refused when the evidence does not identify it
-   Provider retries are surfaced on the spinner (`Provider busy, retrying (2/3)`) instead of appearing as a stalled turn, forwarded over RPC and themeable via `label_retrying`
-   Magic-number detection for binary formats that lead with ASCII (PDF, PostScript), which previously read as pages of mojibake or failed with an unrelated line-count error
-   Headless device-code authentication (RFC 8628) for xAI Grok and OpenAI Codex, plus run-local Google Vertex credentials
-   Session-stable prompt caching, with configurable retention: 1-hour TTL for Anthropic and extended 24-hour retention for OpenAI responses
-   Dynamic model catalog integration with provider-based model discovery; adds the Opus 5 and `kimi/kimi-k3` models
-   External editor support with terminal suspension and round-trip text editing
-   A first-launch setup screen for theme and telemetry configuration
-   RPC mode gains multi-select and tree navigation, queue monitoring, thinking-level controls, multimodal attachments on prompt/steer/follow-up, and an extension UI bridge for early lifecycle access
-   The `autoresearch` example extension, with a TUI dashboard, evaluation tools, iteration hooks and a finalize skill

### Changed

-   Anchor tokens are a flat four hex characters at every file size. Width previously grew with line count to make collisions rare; with collisions detected there is nothing left for it to buy
-   Files larger than the four-hex anchor space are no longer refused. A 70,000-line file must give two lines the same token by pigeonhole, which is now caught rather than prevented
-   A line's anchor is derived from its content and the lines above it, or from its run's identity, rather than from a retry counter assigned in file order
-   `read` escapes characters that would break its own line structure (form feed, vertical tab, the Unicode line separators) and says so in the footer; they were previously emitted raw, splitting one anchored line into two displayed lines
-   The anchor table is cached within a single edit, which stamped the same file up to three times
-   Session memory is bounded by shedding folded message content after compaction, with dynamic residency tracking and rehydration of only the visible window on navigation
-   TUI startup defers heavy imports, streaming render CPU cost is reduced, and session loading, listing and rollback moved off the interactive event loop

### Fixed

-   `edit` no longer reshapes the file it edits: non-UTF-8 files are refused rather than crashing, form feeds inside a line survive, and CRLF files are not rewritten as LF
-   Anchor probing is no longer quadratic on duplicate-heavy files, and duplicate anchors are never emitted
-   Plain-text `Ctrl+V` pastes route through standard input handling, and filesystem errors during file detection no longer abort the paste
-   Project settings reload no longer overrides trust status; project skills, prompts, themes and extensions are gated on trust, and `tau` subcommands default to untrusted
-   A subagent timeout no longer aborts the parent session as a spurious user interrupt
-   `TextLLM` no longer shares the provider registry's `LLMOptions` across instances, and session IDs regenerate when account identity changes to prevent cross-account cache reuse
-   Extension reloads refresh the command palette and no longer leave the footer lifecycle in a stale state

## 0.8.2 — 2026-07-18

### Added

-   Add the `kimi/kimi-k3` model, including image and video input support, a 1M-token context window, and Moonshot's `max` reasoning-effort dialect
-   Add opt-in performance profiling: set `TAU_PROFILE=1` to write aggregate startup, extension, rendering, tool-call, and session-persistence timings to `~/.tau/logs/profile-<pid>-<timestamp>.log` on exit
-   Expose the mutable request headers and provider ID to `BeforeProviderRequestEvent` hooks, and expose raw HTTP status and response headers through `AfterProviderResponseEvent` when the provider supplies them
-   Add configurable tool, parallel-execution, and extension-event-handler timeouts (`tool_timeout_seconds`, `max_parallel_tool_calls`, and `event_handler_timeout_seconds`) with safe defaults

### Changed

-   Remove the experimental `serve` mode and `tau serve` command; supported `--mode` values are now `interactive`, `print`, `json`, and `rpc`
-   Improve terminal LaTeX rendering: bracketed display math (`\[...\]`) and inline parenthesized math (`\(...\)`) are supported, while math is protected from Markdown table parsing and code spans/fences remain literal
-   Resolve extension package directories through distribution metadata, handling packages whose distribution and import names differ, and strengthen package-name normalization and installation error reporting
-   Move session loading, listing, rollback, clone rewrites, and extension imports off the interactive event loop; the resume selector now loads session lists lazily to keep the TUI responsive with large session stores
-   Bound `grep` and `glob` execution to 30 seconds and cap concurrent tool task creation to avoid unbounded resource use

### Fixed

-   Prevent session and authentication persistence deadlocks by centralizing atomic file writes and using asynchronous file locks
-   Prevent extension reloads from hanging on event handlers by applying handler timeouts, and improve extension dependency locking
-   Ensure bounded tool execution cleans up child processes that outlive stdout closure or a timeout, and close model adapters to prevent connection leaks
-   Reclaim TUI focus when detached components are removed or replaced, and reduce retained child-component state and rendering work in terminal scrollback
-   Reuse generated session context across hook calls to avoid redundant context-building work

## 0.8.1 — 2026-07-16

### Added

-   Add per-model `thinking_levels` for every reasoning-capable model across all providers (OpenAI, Anthropic, Google, NVIDIA, Mistral, xAI, Z.ai, DeepSeek, Groq, Cerebras, Fireworks, Perplexity, Hugging Face, Bedrock, GitHub Copilot, Kilocode, MiniMax, Subconscious, OpenRouter), replacing the old singular `thinking_level` field and letting the reasoning-effort picker only offer levels a given model actually supports
-   Add the Tinker provider (Thinking Machines' OpenAI-compatible endpoint), including their new Inkling model, with a dedicated reasoning-effort dialect and audio input support
-   Add a `--effort` CLI flag to temporarily override the thinking level for a single run without touching persisted settings
-   Add `Model.clamp_thinking_level()` and wire it through the effort picker, `/model` switching, and session startup, so a previously-selected level is validated against whichever model is active instead of silently sending an unsupported value

### Changed

-   Replace hardcoded `model.id` string matching in the Anthropic and Gemini/Antigravity backends with explicit per-model capability fields (`thinking_adaptive`, `thinking_suppresses_sampling`, `thinking_uses_level`, `antigravity_is_claude`)
-   Reimplement reasoning-signature replay for the OpenAI Responses API backend (shared by OpenAI, Perplexity, xAI, Bedrock, and Grok CLI), live-verified end-to-end including chained multi-tool-call turns

### Fixed

-   Fix reasoning being silently left enabled when explicitly turned off (Mistral, Ollama, and OpenRouter backends)
-   Fix Gemini 3 models on Vertex AI always receiving a raw token budget instead of the coarse thinking-level control they're designed around
-   Fix `claude-fable-5` and `claude-sonnet-5` requests sending a `temperature` parameter that Anthropic's API rejects for those models
-   Fix a "compaction blob" error from the Grok CLI proxy caused by re-serializing the reasoning signature with unset optional fields materialized as explicit nulls

## 0.8.0 — 2026-07-14

### Added

-   Add web search extension with DuckDuckGo engine and dependency configuration, enabling agents to perform real-time web searches and retrieve current information
-   Add ScrollbackTerminal to manage incremental terminal rendering and viewport updates, improving responsiveness and performance when displaying large output
-   Add model definitions and inference provider implementations for Google Gemini and Antigravity services, expanding multi-provider LLM support
-   Add InputHandler for managing session input, media, and deferred execution, providing a unified interface for user interactions
-   Add modular inference API and local model support, including auto-discovery and integration with local Ollama, LM Studio, vLLM, and llama.cpp instances
-   Add installation and authentication guide documentation for improved onboarding

### Changed

-   Refactor inference provider system to support modular architecture with local model auto-discovery

### Fixed

-   Fix offscreen row-count changes causing unnecessary full scrollback wipes, improving rendering efficiency
-   Improve terminal responsiveness through optimized rendering with frozen transcript row preservation
-   Improve performance through reduced streamed-content flush rate to minimize scroll-follow snaps

## 0.7.9 — 2026-07-13

### Fixed

-   Improve TUI responsiveness around streamed Markdown by freezing completed assistant messages immediately after they finish, while preserving expand/collapse invalidation behavior
-   Reduce active streaming Markdown render work by caching completed top-level blocks at blank-line boundaries outside fenced code blocks

## 0.7.8 — 2026-07-13

### Added

-   Add configurable `computer_use` observation modes for screenshots, accessibility trees, or both, with automatic accessibility-tree fallback when the active model does not support image input
-   Add a `serve` mode with a web UI
-   Add a `doctor` command to diagnose Tau configuration, credentials, Python compatibility, and installed packages, including guided repair support
-   Add opt-in startup timing diagnostics for investigating slow initialization
-   Track cache-write tokens across OpenAI inference providers and expose them in session and footer usage displays

### Fixed

-   Pin managed virtual environments to the Python interpreter running Tau, preventing package installation into an incompatible interpreter
-   Restrict `prompt_cache_options` to the native OpenAI provider so compatible third-party endpoints do not receive unsupported request fields

## 0.7.7 — 2026-07-12

### Added

-   Add a session log file for every run, not just interactive mode — non-interactive modes (`print`, `json`, `rpc`) now attach a `FileHandler` at startup, so unhandled errors are captured instead of only reaching stderr
-   Add cross-platform accessibility tree traversal and window-wise element extraction for the `computer_use` extension, including initial Windows UIA and macOS AX control modules
-   Add `aclose()` to the web engine base class and wire extension unload cleanup, so the DDG engine can reuse a persistent client (fixing DuckDuckGo's 202 challenge page being silently parsed as zero results when a fresh client was opened per search) and the Tavily engine releases its client on unload

### Changed

-   Log unhandled exceptions in the CLI entrypoint and the agent loop via `logging.exception` before re-raising, so crashes are still fatal but the traceback survives in the session log file instead of only reaching stderr

## 0.7.6 — 2026-07-12

### Added

-   Add image support to the `read` tool — a PNG, JPEG, GIF, or WEBP file is detected from its magic bytes (regardless of extension) and returned as image content instead of erroring as binary, up to a 10 MiB cap, and gated on the active model actually accepting image input (falls back to a clear error naming the model instead of silently dropping the image)
-   Add `gemma-4-31b` model configuration to the Cerebras provider
-   Add `RESOURCE_EXHAUSTED` to the rate-limit error keyword list so those errors get retried instead of surfacing as hard failures

### Changed

-   Refactor `APIProvider` definitions in `tau/builtins/providers/text.py` for improved readability
-   Remove unused strict tool-calling infrastructure and constraints

### Fixed

-   Fix `read`, `glob`, `grep`, `ls`, `write`, `edit`, and the sandboxed `terminal` variant's result renderers silently showing a success-shaped summary (e.g. "Read 0 lines", "No files matched") on error instead of the actual error text, because they ignored the `is_error` flag passed to `render_result`
-   Fix tool errors losing their full traceback and being formatted by a tool's custom renderer instead of the plain unhandled-exception path
-   Fix empty thinking blocks that contain a signature being dropped instead of preserved, which lost the signature and broke Anthropic turn replays

## 0.7.5 — 2026-07-11

### Added

-   Add multimodal support for images, audio, video, and file attachments in both user messages and tool results, wired through Anthropic, Gemini (`gemini_generate`, `google_vertex`, `google_antigravity`), and OpenAI Codex Responses with per-provider wire-format conversion, and `Modality.File`/`Modality.Audio` curated per model against real per-provider capability research
-   Add cross-platform clipboard paste for images, audio, video, and file attachments via the `pyxclip` library (Ctrl+V) — replacing the prior Pillow-only, image-only clipboard path, which could never read file references on macOS — plus bare-file-path detection for the Cmd+V-via-terminal-paste case
-   Add audio input support for OpenRouter's proxied audio-capable models via `openai_user_content`
-   Show the user's attempted message in the transcript (with its media placeholder) when attached media isn't supported by the active model, naming exactly which modalities the model does and doesn't support, instead of silently discarding the message with only an error notification
-   Add a `--base-url` non-persistent CLI flag to override provider endpoints for a single run
-   Add a `todo` extension with an ephemeral status board and agent state injection
-   Add auto-discovery of local Ollama, LM Studio, vLLM, and llama.cpp models at startup
-   Add support for OpenAI Codex Responses Lite models with UUIDv7 session affinity and request reformatting

### Changed

-   Refactor tool-result formatting across providers to support multimodal (image/audio/video) tool results generically instead of provider-specific one-offs
-   Standardize system notifications — `/reload`, `/compact`, extension notifications, and TUI input-handler errors — to always include consistent trailing blank-line spacing, matching the behavior slash commands like `/model` already had
-   Update system prompts to enforce evidence-based responses and clarify multi-message handling logic

### Fixed

-   Fix `AudioContent`/`VideoContent`/`FileContent` crashing session persistence on real binary data, and a follow-up double-base64-encoding bug on session reload caused by union-type member ordering
-   Fix PDF/DOCX/XLSX/PPTX attachments being mis-routed to the image store and crashing on paste

## 0.7.4 — 2026-07-10

### Changed

-   Replace estimated spinner token counts with provider-reported usage across `github_copilot_chat`, `openai_completions`, and `openai_vertex`, and update the spinner to use the last assistant turn's real usage as the baseline for pending input tokens instead of incremental estimation
-   Correct several inaccuracies found in `docs/` and `README.md` during a documentation audit (wrong flag names, stale keybindings, incorrect settings keys, outdated theme/provider lists, and a broken code example in the TUI public API guide)

## 0.7.3 — 2026-07-10

### Added

-   Add live per-turn elapsed time and streaming token counts (`↑` input / `↓` output) to the spinner and the footer's context-usage badge, updating in real time as the response streams in rather than only at turn end — token counts render compactly with K/M/B/T suffixes
-   Add a `Working…` spinner state shown while an API call is in flight but no content has arrived yet, so `Thinking…`/`Streaming…` only appear once the model actually starts producing thinking or text content
-   Add the Hugging Face `openai`-compatible inference provider (`huggingface`), routed through `https://router.huggingface.co/v1`, with built-in model entries pinned to a specific backend (`<repo>:<provider>`) rather than the router's `:fastest` default, since unpinned routing can silently switch to a backend with different tool-calling behavior
-   Add GPT-5.6 Sol/Terra/Luna to the `openai` and `openai-codex` model catalogs with corrected context windows (1.05M / 500K) and real per-model pricing
-   Add 65 additional built-in Hugging Face-routed models (DeepSeek, GLM, Qwen, MiniMax, Kimi, Nemotron, and more) across several backends

### Changed

-   Refactor the subagent `context="fork"` mode to read the parent session's messages directly and seed the embedded (in-process) agent's `initial_messages` with them, instead of spawning a `tau --resume ... --ephemeral` subprocess — avoids the subprocess/CLI-argument round trip entirely
-   Replace hardcoded ANSI escape codes in the `ask_user` TUI component with theme-driven colors (`LayoutTheme`), so its header, hints, and scroll indicator now follow the active theme like the rest of the TUI
-   Update TUI selection and list styles to use accent/emphasis colors instead of reversed backgrounds
-   Simplify the todo board to a header-less, shell-framed list, matching tool-result framing elsewhere; completed items now render with strikethrough

### Fixed

-   Fix the `google-antigravity` provider never reading Gemini's `usageMetadata` field from its raw SSE stream, so token usage was always reported as zero for any model routed through it (Gemini or Claude)
-   Fix `thoughts_token_count` (thinking-model output) and `tool_use_prompt_token_count` (tool-result tokens fed back as input) not being read by any of the three Gemini-family providers (`gemini_generate`, `google_vertex`, `google_antigravity`), undercounting usage on any thinking-enabled turn
-   Fix `openai_vertex` never extracting `cache_read_tokens` at all, unlike its sibling `openai_completions`
-   Fix `mistral_chat`'s cache-read-token extraction being dead code — the Mistral SDK doesn't type `prompt_tokens_details` as a sub-model, so it arrived as a raw dict and attribute access on it silently always returned 0
-   Fix `anthropic-claude-code` OAuth token refresh failing with `invalid_grant` when Claude Code (the CLI/app) had rotated the refresh token more recently than Tau — now falls back to the OS Keychain's latest credential instead of forcing a manual re-login

## 0.7.1 — 2026-07-10

## 0.7.2 — 2026-07-12

### Added
- Release the 0.7.2 version to PyPI.
- Bump internal dependency versions for security fixes.
- Minor documentation updates for new features.
### Added

-   Add a `tau update` command that upgrades Tau (and installed extension packages) using the installer that matches how this copy was installed — inferred from the venv it runs in — with an animated indeterminate progress bar on stderr while the blocking install runs, so a slow download no longer looks like a hung terminal
-   Add Claude Fable 5 and Claude Opus 4.8 to the `anthropic` (API key), `anthropic-claude-code` (OAuth), and `anthropic-vertex` model catalogs
-   Expose Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`) through the `anthropic-claude-code` OAuth provider, so a Pro/Max login can select it directly instead of only via `anthropic-vertex`
-   Flesh out the `google-antigravity` provider catalog with the current model line-up — Gemini 3.1 Pro (High), Gemini 3.5 Flash (High), Claude Sonnet 4.6 / Opus 4.6 (Thinking), and GPT-OSS 120B (Medium) — including per-model `max_output_tokens` and corrected context windows

### Fixed

-   Fix `anthropic-claude-code` OAuth on Windows: Claude Code CLI writes its token to plaintext JSON at `~/.claude/.credentials.json` (honoring `CLAUDE_CONFIG_DIR`) and never uses Windows Credential Manager, so the old `PasswordVault` WinRT lookup always came back empty — read the file directly instead
-   Correct stale Anthropic pricing: Opus 4.7 was still at the old $15/$75 Opus-4.1-era rate (now $5/$25), Sonnet 5 now reflects its introductory $2/$10 rate (standard $3/$15 resumes 2026-08-31), Haiku 4.5 was under-priced, several `anthropic-vertex` Fable 5 / Opus 4.5–4.8 prices were swapped or stale, and the 4.6+ generation context windows were bumped to the 1M-token (1,048,576) window Anthropic ships
## 0.7.0 — 2026-07-09

### Changed

-   Defer the LSP eager-detection `shutil.which()` availability check until a server's language is actually detected in the project, instead of checking every registered server upfront — servers for languages the project doesn't use (e.g. clangd/rust-analyzer/gopls in a pure-Python repo) never pay that PATH×PATHEXT scan at all

### Fixed

-   Fix the sandbox extension's microVM only being stopped on `session_shutdown` (which fires on session transitions like new/resume/clone) and not on actual process exit, leaving the booted microsandbox subprocess running until its own `idle_timeout_seconds` (default 30 min) — it's now also stopped on `runtime_stop`, matching the LSP extension's existing shutdown handling
-   Fix the LSP client's `_ws_diag_cache` (workspace-diagnostics cache) never being evicted in `close_file()`, unlike its four sibling per-file caches, so it grew for the life of the client with one entry per file ever reported by `workspace/diagnostic`

## 0.6.9 — 2026-07-10

### Added

-   Add an optional `tools` dependency group (`pip install tau-coding-agent[tools]`) that bundles a prebuilt `ripgrep` binary, since `Grep`/`Glob` silently fail with "ripgrep (rg) is required but was not found" when no system-wide `rg` is on `PATH`

### Changed

-   Replace `difflib.SequenceMatcher` with `rapidfuzz.distance.Indel` for word-level diff highlighting, avoiding difflib's pathological worst-case slowness on long lines

### Fixed

-   Fix the CLI eagerly importing the full agent/engine/pydantic graph even for trivial commands like `--version`/`--help`, adding ~250-400ms to every invocation regardless of what was actually requested
-   Fix `_detect_shell()` falling through to up to four `shutil.which()` PATH scans on Windows (where `$SHELL` is never set), each scanning every `PATH` entry against every `PATHEXT` suffix for POSIX shells that don't exist there
-   Fix a failed extension dependency install (e.g. a dependency with no prebuilt wheel for a free-threaded Python build) retrying the full failing subprocess build on every single startup instead of failing fast after the first attempt
-   Fix OAuth providers (`xai-grok`, `openai-codex`, `google-antigravity`, `anthropic-claude-code`, `github-copilot`) eagerly importing their provider SDK when resolving `.api` instead of using the existing lazy registry-key mechanism, deferring the import to the first actual request
-   Fix the background PyPI version check constructing its HTTP client (and SSL context) inline on the event loop, which could stall the just-launched TUI's render loop for several hundred ms on Windows

## 0.6.8 — 2026-07-09

### Added

-   Add `xai-grok` OAuth provider giving SuperGrok/X Premium+ subscribers quota-based access to Grok models (`grok-4.5`, `grok-4.3`, `grok-build`) via the Grok CLI's `cli-chat-proxy.grok.com` backend, reusing `~/.grok/auth.json` when the official Grok CLI is already logged in
-   Refresh the Mistral model catalog: reprice and widen the context window for Mistral Medium 3.5 and Small 4, split Devstral into `devstral-medium-latest`/`devstral-small-latest`, add the Ministral 3 (14B/8B/3B) and Leanstral 1.5 models, and drop the now-deprecated Magistral line
-   Add the Grok 4.5 model to the xAI catalog

### Fixed

-   Fix `openai_responses` sending `function_call` and thinking content nested inside a message's `content` array instead of as top-level input items, which the OpenAI Responses API schema requires — worked against OpenAI's and xAI's lenient API-key backends but caused `422 Failed to deserialize... untagged enum ModelInput` against the stricter Grok CLI proxy
-   Fix `openai_responses` looking up a streamed tool call's name by the wrong id (`item_id` instead of `call_id`), which silently resolved to an empty name and caused `Tool '' not found` errors on any Responses-API backend where the two ids differ (e.g. the Grok CLI proxy)

## 0.6.7 — 2026-07-09

### Fixed

-   Fix `ddgs` engine dependency name in the web extension manifest (`AsyncDDGS` → `asyncddgs`), which caused `web_search` to fail with `No module named 'asyncddgs'` after a fresh install

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
