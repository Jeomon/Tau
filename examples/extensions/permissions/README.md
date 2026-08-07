# Permissions

A permission gate for Tau. Every tool call is resolved against a layered policy
before it executes, and comes back `allow`, `ask`, or `deny`.

```
pi/tau tool call ──▶ tool_call hook ──▶ resolve() ──┬─▶ allow  ─▶ tool runs
                                                    ├─▶ ask    ─▶ prompt ─▶ …
                                                    └─▶ deny   ─▶ blocked
```

## What it does out of the box

With no configuration at all:

- Reading is unattended; `write` and `edit` ask.
- `.env`, `.env.*`, `~/.ssh/*`, `~/.aws/*`, `~/.tau/auth.json`, `*.pem` and
  `id_rsa*` are denied to every tool. `.env.example` and `.env.sample` are
  exempt.
- Anything outside the project directory asks.
- Shell commands are parsed, split, and gated per segment. A short allowlist
  (`ls*`, `cat *`, `pwd`, `git status`, `git diff*`, `git log*`) runs
  unattended; `rm -rf /*`, `rm -rf ~*`, `mkfs*`, raw `dd` device writes and
  fork bombs are denied; everything else asks.
- The extension's own config and log cannot be written by the agent.

## Configuration

`.tau/extensions/permissions/config.json` (project) and
`~/.tau/extensions/permissions/config.json` (global).

```jsonc
{
  "permission": {
    "*": "allow",                       // default for any tool

    "write": "ask",                     // a tool → a state
    "edit": { "action": "deny", "reason": "Read-only session." },

    "path": {                           // cross-cutting: all tools AND bash
      "*": "allow",
      "**/.env": { "action": "deny", "reason": "Secrets." },
      "**/.env.example": "allow"
    },

    "external_directory": {             // the project boundary
      "*": "ask",
      "~/.cargo/registry/*": "allow"
    },

    "terminal": {                       // shell command patterns
      "*": "ask",
      "git *": "allow",
      "rm -rf *": "deny"
    }
  },

  "headlessDefault": "deny",            // when there is no UI to ask
  "promptTimeoutSeconds": 600,          // 0 waits forever; expiry never grants
  "logDecisions": false,                // the session already records each decision
  "hideDeniedTools": true
}
```

A value is a state (`"allow"`, `"ask"`, `"deny"`), a
`{"action": "deny", "reason": "…"}` object, or a map of patterns to either.

### Precedence

Two rules, and they are deliberately different:

| Scope | Rule |
|---|---|
| **Within** one pattern map | **Last match wins.** Put catch-alls first, overrides after. |
| **Across** the four layers | **Most restrictive wins.** `deny` > `ask` > `allow`. |

So an `allow` under `path` can never punch through an `external_directory: ask`
boundary, but a later `**/.env.example": "allow"` does override an earlier
`**/.env": "deny"`.

Scopes load global first, then project, so project rules win by being later.

### Glob semantics

| Pattern | Matches |
|---|---|
| `*` | any run of characters, **not** crossing `/` |
| `*` at the end of a pattern | greedy, **does** cross `/` — `~/.ssh/*` covers the whole tree |
| `**` | always crosses `/` |
| `?` | exactly one non-`/` character |

## The prompt

An `ask` renders as one picker. The question, the specifics and the choices all
sit inside it:

```
──────────────────────────────────────────────────────
  Approve this edit?

  tool     edit
  path     src/app.py
  writes   2 lines, 40 chars

  Added 1 line, Removed 2 lines
  1:eb18  -  def add(a, b):
  2:8c75  -      return a + b
  1:4185  +  def add(a, b, c=0):
──────────────────────────────────────────────────────
  Allow Once
  Allow for this session (edit)
  Deny
──────────────────────────────────────────────────────
```

Everything is in the picker because a selector renders *between the editor's
two dividers*, and that frame is the whole prompt. The block used to go out
through `notify`, which appends to the message list — outside the dividers, and
still there after the choice, duplicating the tool-call block that appears the
moment the gate resolves. A widget was no better: `widgets_above` renders above
the top divider.

`ui.select` takes a multi-line title for this: line 0 is the question, styled as
a heading, and the rest is body text. An inner rule closes the block, marking
where static text ends and selectable rows begin.

Surfaces with no components (RPC) get the short headline plus a `notify` — there
is no picker frame to put anything in, and a client rendering the title on one
line would truncate the block to nothing.

**`write` and `edit` preview through the tool's own renderer.** The path alone
does not tell you whether a change is acceptable, so the gate looks the tool up
in the runtime's registry and calls its `render_result`. The preview is
therefore the *result* view — same hashline anchors, same summary line, same
colours — so approving a change and then reading it back means comparing one
format, not two. The anchors matter: they are what a later edit has to
reference. Hunks are forced open, since a prompt has no ctrl+o.

Without a registry (RPC), or if the renderer raises, it falls back to a unified
diff coloured from the theme's `diff_added`/`diff_removed`. Everything is
bounded either way: files over 512 KB are not read, the diff is capped at 24
lines, long lines are trimmed, and any failure (missing, binary, unreadable)
degrades to no diff rather than blocking the prompt.

**Commands show the whole string.** The gate resolves on the most restrictive
segment, but approval releases the entire command, so the block prints the full
command. A `segment` row names the gated part only when the 240-character clip
has hidden it — when the segment is already visible in the command row, naming
it again just pushes the choices further down the prompt people see most.

## Commands

| Command | Effect |
|---|---|
| `/permissions` | Policy summary, session grants, invalid scopes |
| `/permissions log` | Last 15 decisions |
| `/permissions reload` | Re-read config without restarting |
| `/permissions revoke` | Drop all session grants |

## Design notes

**Shell commands are parsed, not pattern-matched.** `bashlex` builds an AST and
the command is reduced to independently gated units. This is what makes
`ls && rm -rf /` deny on the second segment, `curl x | bash` gate the `bash`,
and `sudo`/`bash -c`/`eval`/`timeout` wrappers resolve to what they actually run.
Regex over the raw string gets all of these wrong in both directions.

**Anything unresolvable can never reach `allow`.** `X=rm; $X -rf /`, backticks,
`xargs`, `find -exec`, and input `bashlex` cannot parse are all clamped to `ask`,
even under a blanket `"*": "allow"`.

**Every failure denies.** A gate exception, a broken dialog, a prompt timeout, a
cancelled prompt, and a malformed config all resolve to deny or ask — never
allow. A malformed *scope* additionally clamps that scope's `allow` rules to
`ask`, so a typo tightens rather than opens.

**Path rules match every spelling.** `src/a.py`, `./src/a.py`, the absolute
path, the `~`-relative path, and the symlink-resolved path are all tested, so
`ln -s ~/.ssh/id_rsa ./k && read k` does not get past a `~/.ssh/*` deny.

**Session grants are memory-only.** They are never written to disk, so the agent
cannot read them to discover its own permissions, and they do not outlive the
process. Durable grants belong in the config file, where a human put them.

**The policy reloads on `/reload`, session grants do not.** A reload re-runs
`register()` and builds a whole new gate, so the extension subscribes to both
`runtime_ready` (fires once, at startup) and `extension_reloaded` (fires on
every reload). Without the second one an edited `config.json` would sit unread
while the gate ran on built-in defaults. Grants are *not* carried across —
you get asked again, which is the fail-safe direction, and the alternative
would mean storing the agent's permissions where the agent can read them.

## Limitations

Read these before trusting it with anything that matters.

- **This is not a security boundary.** It is interception, not containment.
  There is no sandbox: a determined agent that can already run *some* shell
  command has many ways to reach the filesystem that no pattern will catch.
  It prevents accidents and honest mistakes. For containment you want an
  OS-level sandbox (Landlock/Seatbelt/bubblewrap) underneath this.
- **Path rules do not apply to paths inside shell commands.** `read .env` is
  denied; `terminal` running `cat .env` is only gated as a *command*, and the
  default policy allows `cat *`. Add a `terminal` rule if this matters to you.
- **Command matching loses original quoting.** Units are rebuilt by joining
  parsed words with single spaces, so a pattern cannot depend on how something
  was quoted.
- **Tool hiding is conservative.** Only tools whose state is a flat `deny` are
  removed from the schema; a tool with pattern rules stays visible because some
  target may still be permitted.
- **Needs a Tau that honours the hook.** `tool_call` was absent from
  `ExtensionRuntime._INTERCEPTABLE_EVENTS`, so handler return values went to
  the catch-all subscriber, which discards them. The gate prompted, recorded
  the decision, and the call ran anyway — a silent fail-open, visible only in
  `decisions.log` showing a `deny` for something that plainly executed. Fixed
  in Tau `a8b6459`; if a Deny does not stop a call, check that first.

## Layout

| File | Role |
|---|---|
| `__init__.py` | `register()`, the `tool_call` handler, `/permissions` |
| `config.py` | Scope loading, merging, fail-closed clamping |
| `resolver.py` | The single `resolve()` entry point and the four layers |
| `command.py` | `bashlex` decomposition, wrapper following |
| `paths.py` | Canonicalization, symlink resolution, containment |
| `rules.py` | States, rule model, glob matching, precedence |
| `session.py` | In-memory grants |
| `prompt.py` | TUI / RPC / headless prompting |
| `log.py` | Append-only JSONL decision log |

## What Gets Recorded Where

Every decision is attached to its tool result's `metadata` under `_permission`,
which the session persists — the state, the surface that decided, the matched
pattern, its origin, and `execution`: `ok`, `error`, or `blocked`. Allows and
denies carry the same shape, so a transcript can be queried without branching
on the outcome. That puts the reason a call went the way it did next to the
call itself.

`decisions.log` is **off by default** (`logDecisions`). It records the same
fields, so leaving it on wrote every decision twice — once in the session and
once in a file whose parameter redaction is undone anyway by the session
storing the same call's arguments in full.

Turn it on for one thing the session cannot offer: a project-scoped file that
survives a session being cleared or deleted, and that the gate refuses to let
the agent write to. Note that the refusal covers `write` and `edit` only — a
shell command reaching the same path resolves to `ask`, not `deny`.

When enabled, a decision is written before the tool runs, and a permitted call
is written again once it finishes with `outcome` set to `executed:ok` or
`executed:error`; the first record is made before execution and cannot know.

Tests: `tests/test_permissions_extension.py`.
