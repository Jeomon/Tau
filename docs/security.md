# Security

Tau is a local agent. It runs as the user account that starts it and can read, write, and execute anything that account can. This document covers the two boundaries Tau actually implements (project trust and telemetry) and states plainly what each does not cover.

## Table of Contents

- [Project Trust](#project-trust)
- [When Tau Asks](#when-tau-asks)
- [What Trusting a Project Permits](#what-trusting-a-project-permits)
- [What Trust Does Not Cover](#what-trust-does-not-cover)
- [The Trust File](#the-trust-file)
- [Default Trust Policy](#default-trust-policy)
- [Overriding Trust for One Run](#overriding-trust-for-one-run)
- [Trust in Subcommands](#trust-in-subcommands)
- [No Built-in Sandbox](#no-built-in-sandbox)
- [Telemetry](#telemetry)
- [Reporting Security Issues](#reporting-security-issues)

## Project Trust

Project trust controls whether Tau treats files inside the working directory as configuration it is willing to load. It is an input-loading guard, not a sandbox: it decides whether a repository can change Tau's settings and system prompt before you start working, and nothing more.

Trust is per directory and inherits downwards. Decisions are keyed by resolved absolute path, and Tau walks upward from the working directory to find the nearest stored decision, so trusting `~/work` covers `~/work/project-a/src` unless a closer entry says otherwise.

## When Tau Asks

Tau only considers a trust decision necessary when the directory actually contains something that requires one. Starting from the working directory and walking up to the filesystem root, it looks for:

| Input | Condition |
|-------|-----------|
| `.tau/` directory | Exists in the directory and is not the user's own global `~/.tau` |
| `.agents/skills/` directory | Exists in the directory |
| Context files (`AGENTS.md`, `CLAUDE.md`) | Present in any directory from the git root down to the working directory |

The context-file search is bounded: if the working directory is inside a git repository, directories from the git root down to the working directory are checked; if it is not, only the working directory is checked. The `.tau/` and `.agents/skills/` checks continue all the way to the root.

If none of these are found, the project is treated as trusted without prompting. There is nothing to decide.

In interactive mode, when a decision is needed and none is stored, Tau replaces the UI with a trust screen before the session begins. The choices are:

| Option | Persisted | Effect |
|--------|-----------|--------|
| Trust | Yes, for this directory | Loads project resources |
| Trust parent folder (`<parent>`) | Yes, for the parent directory | Loads project resources; also removes any stored entry for the current directory |
| Trust (this session only) | No | Loads project resources; nothing is written to disk |
| Do not trust | Yes, `false` for this directory | Tau exits |

Declining or dismissing the prompt stops the application rather than continuing in a reduced mode.

Until trust is resolved, session persistence is held back: the session directory is not created and nothing is written. Approving trust enables persistence, reloads extensions so project configuration takes effect, and restores the normal layout.

Non-interactive modes (`--mode print`, `--mode json`, `--mode rpc`) never show the prompt. They resolve trust from the CLI flags, then the policy setting, then the stored decision, and with the default `"ask"` policy and no stored decision, the project is treated as untrusted.

## What Trusting a Project Permits

Trust is consumed at these points, all verified in the runtime:

| Granted by trust | Mechanism |
|------------------|-----------|
| Project settings are merged | `.tau/settings.json` is only exposed when the project is trusted; otherwise project settings are empty |
| Project context files reach the system prompt | `AGENTS.md` / `CLAUDE.md` discovery is enabled only when trusted |
| The git snapshot reaches the system prompt | The `git status` section is skipped when the project is untrusted |
| Project skills, prompts, and themes load | `.tau/skills/`, `.tau/prompts/`, and `.tau/themes/` load only when trusted. Skills and prompts are model-executable instructions, so an untrusted project contributes none |
| Project extensions load | `.tau/extensions/` is included in the resource snapshot only when trusted. Extensions are arbitrary Python executed in-process |

Builtin and global resources are unaffected: an untrusted project still gets the bundled skills, your `~/.tau/skills/`, and every globally configured extension. Only the project tier is withheld.

Granting trust mid-session takes effect immediately: accepting the trust prompt reloads settings, resources, and the system prompt without a restart.

Because project settings are the carrier for several other things, trusting a project transitively enables everything they configure:

- project package entries (`packages.list` in `.tau/settings.json`) and the resources those packages bundle, see [Packages](packages.md)
- project extension entries (`extensions.list`) and their per-extension settings
- every other project-scoped setting value, including model selection and tool configuration, see [Settings](settings.md)

Extensions can participate in the decision. `await ctx.is_project_trusted()` consults the `project_trust` hook before falling back to the settings manager, and `ctx.set_project_trusted(trusted, remember=True)` writes the decision to the trust file. See [Extensions](extensions.md#project-trust).

## What Trust Does Not Cover

Trust gates which project-local files Tau loads. It does **not** constrain the model or its tools. Once a session is running, built-in tools read files, write files, and run shell commands with the full permissions of the Tau process. Prompt injection from repository files, comments, documentation, dependency source, or build output is expected local-agent risk and cannot be reliably prevented.

## The Trust File

Decisions live in `~/.tau/trust.json`: a flat JSON object mapping resolved absolute directory paths to booleans.

```json
{
  "/home/user/work": true,
  "/home/user/work/vendor-fork": false,
  "/home/user/scratch": true
}
```

| Property | Behaviour |
|----------|-----------|
| Keys | Absolute, resolved (symlinks followed) directory paths |
| Values | `true` (trusted) or `false` (explicitly untrusted) |
| Lookup | Nearest ancestor wins; the walk stops at the first `true` or `false` |
| Writes | Serialized by a `trust.json.lock` file lock, then written atomically |
| `null` values | Stripped on write: an entry is either a decision or absent |
| Corrupt file | Copied to `trust.json.corrupt-<epoch>`, then reset to empty; a warning is logged |

The file is safe to edit by hand. Deleting an entry makes Tau ask again for that directory; deleting the file resets every decision.

## Default Trust Policy

The global-only `project_trust` setting decides what happens before any prompt is shown.

| Value | Behaviour |
|-------|-----------|
| `"ask"` | Default. Use the stored decision; if there is none, prompt in interactive mode and treat as untrusted elsewhere |
| `"always"` | Trust every project. The trust file is not consulted |
| `"never"` | Trust no project. The trust file is not consulted |

```json
{
  "project_trust": "always"
}
```

Set it in `~/.tau/settings.json`, or interactively through `/settings` → **Project trust**. It is deliberately global-only, so a repository cannot raise its own trust level through project settings.

Resolution order at startup:

1. If the directory has no trust inputs, it is trusted.
2. Otherwise, if `--approve` or `--no-approve` was passed, that wins.
3. Otherwise apply `project_trust`: `"always"` → trusted, `"never"` → untrusted, `"ask"` → the nearest stored decision.
4. Under `"ask"` with no stored decision, the project starts untrusted and the interactive trust screen is scheduled.

## Overriding Trust for One Run

| Flag | Short | Effect |
|------|-------|--------|
| `--approve` | `-a` | Trust project-local files for this run |
| `--no-approve` | `-na` | Do not trust project-local files for this run |

```bash
tau --approve                      # Trust this project for one interactive run
tau --no-approve -p "summarise"    # Run a prompt without loading project config
tau --mode rpc --approve           # Non-interactive modes never prompt; be explicit
```

Neither flag writes to `~/.tau/trust.json`; both apply only to the process they start. If both are passed, `--approve` wins.

Related flags: `--no-context-files` / `-nc` disables `AGENTS.md` and `CLAUDE.md` loading independently of trust, and `--system` / `-s` replaces the generated system prompt entirely. See [CLI Reference](cli-reference.md).

## Trust in Subcommands

`tau doctor`, `tau list`, `tau install`, `tau remove`, and `tau update` also honour project trust. None of them can prompt, so they resolve the decision the same way non-interactive modes do: a directory with no trust inputs needs none, otherwise the `project_trust` policy decides, with `"ask"` falling back to the stored decision and to untrusted when none exists.

An untrusted project contributes no `.tau/settings.json`, which means its package entries are invisible to these commands:

| Command | Behaviour in an untrusted project |
|---------|-----------------------------------|
| `tau update --all`, `tau update NAME --local` | Project packages are not in the update set |
| `tau install --local`, `tau remove --local` | Refused with an error; run tau in the directory and approve first |
| `tau list --local`, `tau list --all` | Project packages are omitted, with a note saying so |
| `tau doctor` | Project settings and project package entries are not reported |

This matters because a package entry carries its own `index_url` and `extra_index_urls`, which are passed to `pip install` when the package is updated. Honouring those from a repository you have not trusted would let it choose the index that code is fetched from. Global packages are unaffected.

## No Built-in Sandbox

Tau does not include a sandbox. Built-in tools, extensions, package code, and shell commands all run as ordinary processes with the permissions of the user account. Extensions are Python modules that Tau imports and executes; packages can install dependencies into a managed venv at load time.

For untrusted repositories, unattended automation, or generated code you do not intend to review closely, run Tau inside a container or VM with only the files and credentials the task needs. Real isolation has to come from the operating system, not from the agent process.

## Telemetry

Tau sends telemetry to PostHog (`https://us.i.posthog.com`). One settings flag controls all of it.

### What Is Sent

Two things are enabled together, and only when telemetry is on:

| Item | Payload | Frequency |
|------|---------|-----------|
| Install/update ping | A PostHog event named `tau` with a single property, `{"version": "<tau version>"}`, and the literal distinct id `"anonymous"` | Once per installed version |
| Uncaught-exception autocapture | Handled by the PostHog SDK's exception autocapture, which replaces `sys.excepthook` and `threading.excepthook` for the life of the process and reports uncaught exceptions, including type, message, and traceback frames | Per uncaught exception |

The install ping is the entire first payload: no user id, no machine id, no hostname, no path, no OS field, no session content, and no prompt or model data is added by Tau. The distinct id is the constant string `"anonymous"` for every user, so pings cannot be grouped into per-user profiles.

Traceback frames in an autocaptured exception can include absolute file paths and source context from the machine, which is more identifying than the version ping. If that matters for your environment, turn telemetry off.

### Delivery and Deduplication

- The ping is sent in `sync_mode` from a daemon thread, with a 5-second timeout, and never delays process exit.
- After a successful send, the version string is written to `~/.tau/telemetry-version`: file mode `0600`, parent directory `0700`. The marker is written only on success, so a failed send is simply retried at the next startup.
- If that file already contains the current version, nothing is sent.
- Every failure path returns silently; telemetry cannot break a run.
- PostHog's own `atexit` flush hook is unregistered, so a crash cannot hang shutdown waiting on the queue.
- On shutdown, a still-running telemetry task is cancelled.

### Disabling Telemetry

```json
{
  "telemetry": false
}
```

Put this in `~/.tau/settings.json`, or use `/settings` → **Telemetry** inside a session. The setting defaults to `true`.

It is read from global settings only. Project settings cannot re-enable telemetry after a user has disabled it. Setting it to `false` suppresses both the install ping and the exception autocapture, since the same check gates both: no PostHog client is constructed at all.

There is no environment variable for this; the setting is the only switch.

## Reporting Security Issues

Report vulnerabilities by email to **jeogeoalukka@gmail.com** rather than the issue tracker, following the repository [Security Policy](../SECURITY.md). Include a description, reproduction steps, potential impact, and a suggested fix if you have one.

That policy also documents the project's supply-chain practices: exact version pinning, `uv.lock` as the source of truth, and auditing with `pip-audit` or `safety` before release.

Generally outside the security boundary, unless a report demonstrates a real privilege-boundary bypass or shows Tau granting access the local user did not already have:

- the absence of a sandbox, and tools acting with user permissions
- prompt injection from untrusted repository content
- behaviour of extensions, packages, or skills the user chose to install

## Next Steps

- [Packages](packages.md): installing third-party resources and what they can bundle
- [Extensions](extensions.md): the `project_trust` hook and the trust context API
- [Settings](settings.md): `project_trust`, `telemetry`, and scope precedence
- [Project Context](project-context.md): how `AGENTS.md` and `CLAUDE.md` are discovered
- [CLI Reference](cli-reference.md): `--approve`, `--no-approve`, and the rest
