# Sandbox

Run the agent's `terminal` commands inside a [microsandbox](https://github.com/superradcompany/microsandbox)
microVM instead of a host subprocess — hardware-level isolation (its own
guest kernel) rather than just a restricted host process, similar in intent
to the [pi coding agent's reference `sandbox` extension](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/sandbox),
which uses OS-level sandboxing (`sandbox-exec`/bubblewrap) for the same goal.

This extension also ships as a builtin (`tau/builtins/extensions/sandbox`),
enabled by default. This copy is the standalone reference version showing
the manual install path below.

## How it works

Registers a tool named `terminal` — same schema, description, and prompt
guidance as the built-in one, so the agent's interface is unchanged. Only
`execute()` differs: the command runs via `sandbox.exec("sh", ["-c", cmd])`
inside a microVM instead of `asyncio.create_subprocess_exec` on the host.
Falls back transparently to the real built-in `terminal` tool whenever the
sandbox can't be used (unsupported platform, missing dependency, boot
failure), so the agent is never blocked by sandboxing not being available.

## Structure

```
sandbox/
├── README.md         # This file
├── __init__.py        # The extension (entry point, register(tau))
├── manager.py          # SandboxManager: microVM lifecycle
├── sandbox_tool.py      # The tool: same interface as built-in terminal
└── manifest.json         # Settings schema + declares the microsandbox dependency
```

## Installation

From the repository root, symlink the files:

```bash
mkdir -p ~/.tau/extensions/sandbox
ln -sf "$(pwd)/examples/extensions/sandbox/__init__.py" ~/.tau/extensions/sandbox/__init__.py
ln -sf "$(pwd)/examples/extensions/sandbox/manager.py" ~/.tau/extensions/sandbox/manager.py
ln -sf "$(pwd)/examples/extensions/sandbox/sandbox_tool.py" ~/.tau/extensions/sandbox/sandbox_tool.py
ln -sf "$(pwd)/examples/extensions/sandbox/manifest.json" ~/.tau/extensions/sandbox/manifest.json
```

Then register the extension in `~/.tau/settings.json`:

```json
{
  "extensions": {
    "list": [
      { "path": "~/.tau/extensions/sandbox" }
    ]
  }
}
```

The `microsandbox` Python package (declared in `manifest.json`) is installed
automatically the first time the extension loads.

## Requirements

- macOS on Apple Silicon, Linux with KVM, or Windows with WHP (preview).
  On an unsupported host the extension logs a warning once and falls back
  to the real built-in `terminal` tool — it never blocks the agent.
- Network access on first use, to pull the configured OCI image (cached
  afterward) and, if needed, install the bundled `msb` runtime.

## Settings (`/settings` → Sandbox, or `manifest.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Route `terminal` execution through the sandbox. |
| `image` | `"python"` | OCI image the microVM boots from (`python`, `alpine`, `node`, `ubuntu`, ...). |
| `cpus` | `1` | Virtual CPUs allocated to the sandbox. |
| `memory_mib` | `1024` | Memory allocated, in MiB. |
| `persistent` | `true` | Reuse one microVM for every command in the session instead of booting a fresh one per command. |
| `network` | `"public"` | `"public"` allows outbound network access; `"none"` fully isolates the sandbox. |
| `idle_timeout_seconds` | `1800` | The runtime auto-stops the microVM after this much inactivity, so an abandoned/crashed session doesn't leave it running forever. |
| `prewarm` | `true` | Start booting the microVM in the background at session start instead of on the first command, so that command doesn't pay boot latency. |

## Filesystem

The project's working directory is bind-mounted into the guest at
`/workspace`, and commands run with that as their cwd — relative-path
commands behave the same as on the host. Absolute host paths outside the
project root are not reachable from inside the microVM; this is an inherent
tradeoff of sandboxing, not a bug (the pi reference extension has the same
limitation for paths outside its `filesystem.allowWrite` allow-list).

## Concurrency and lifecycle

- The sandbox's name mixes in the running process's PID
  (`tau-<cwd-hash>-<pid>`), so a subagent — a separate `tau` process,
  possibly sharing the parent's cwd — never collides with (and clobbers)
  the parent session's live VM.
- Sandboxes are created with the runtime's own `idle_timeout` and
  `ephemeral=True`, so one abandoned by a crashed process stops and is
  removed on its own rather than running forever — no dependency on any
  Tau process staying alive to clean it up.
- If a command hits a stopped/missing sandbox (idle-timeout fired, or it
  was removed externally), the tool reboots it once and retries the
  command automatically before surfacing an error.
- `/sandbox` shows current status and settings; `/sandbox reset` stops the
  running microVM early so the next command boots a fresh one.
- On cancel (Ctrl+C): if `persistent` is off, the whole sandbox is reset
  (which kills the in-flight command); if `persistent` is on, only the
  in-flight exec is abandoned — the microVM and any state in it survive
  for the next command.

## Limitations

- No live output streaming — unlike the built-in `terminal` tool, output
  appears only once the command finishes (capped at 50 KB / 2,000 lines,
  same as the built-in). This trades away incremental UI updates for a
  much simpler and easier-to-reason-about execute() path.
- Only one OCI image per session; there's no per-call image override.
