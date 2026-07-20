# Quickstart

This page takes you from an empty shell to a working Tau session. For the full install matrix, credential precedence, and troubleshooting, see [Installation](installation.md).

## Install

Tau requires Python 3.12 or 3.13 and installs the `tau` command:

```bash
pip install tau-coding-agent
```

Or from a clone, in editable mode:

```bash
git clone https://github.com/jeomon/tau.git
cd tau
pip install -e .                  # code changes take effect without reinstalling
```

Verify:

```bash
tau --version
```

## Authenticate

Set an API key for one provider. The variable name is the provider id in uppercase plus `_API_KEY`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Other common choices:

```bash
export OPENAI_API_KEY=sk-...       # then: tau --provider openai
export GOOGLE_API_KEY=...          # then: tau --model google/gemini-2.5-flash
export GROQ_API_KEY=gsk_...        # then: tau --provider groq
```

Alternatively store the key so you do not need the export each time:

```bash
tau auth set anthropic sk-ant-...  # writes ~/.tau/auth.json
tau auth status                    # confirm what Tau resolves
```

Stored credentials take precedence over environment variables. See [Authentication](auth.md) for OAuth subscription logins, and [Inference Providers](inference-providers.md) for the full provider list.

## First Session

Run Tau from the directory you want it to work in:

```bash
cd /path/to/your/project
tau
```

Tau starts on `anthropic/claude-sonnet-4-6` unless a different model is saved in settings or passed on the command line. Type a request and press Enter:

```text
Summarize this repository and tell me how to run its checks.
```

By default the agent has seven tools:

| Tool | Purpose |
|------|---------|
| `read` | Read files |
| `write` | Create or overwrite files |
| `edit` | Patch files |
| `terminal` | Run shell commands |
| `glob` | Find files by pattern |
| `grep` | Search file contents |
| `ls` | List directory contents |

Tau runs in your working directory and can modify files there. Use git or another checkpointing workflow if you want easy rollback.

Restrict the toolset for a read-only run:

```bash
tau --tools read,grep,glob,ls      # allowlist; the agent cannot write or run commands
```

## Common Things to Try

### Reference files

Type `@` in the editor to fuzzy-search project files and insert a reference. Browse with the arrow keys and press Tab to select.

On the command line, `@path` arguments attach file contents to the prompt:

```bash
tau --print "Explain this file" @src/main.py
tau --print "Compare these" @src/old.py @src/new.py
```

Images, audio, and video files pasted into the editor are attached as media rather than text. Recognized extensions include `.png`, `.jpg`, `.gif`, `.webp`, `.heic` for images; `.mp3`, `.wav`, `.m4a`, `.flac`, `.opus` for audio; and `.mp4`, `.mov`, `.mkv`, `.webm` for video.

### Run a shell command

Prefix input with `!` to run a command immediately without involving the model:

```text
!pytest -q
```

### Switch models

Use `/model` inside a session, or set one at startup:

```bash
tau --model claude-sonnet-4-6           # model id alone
tau --model openai/gpt-4o               # provider/model shorthand
tau --provider groq                     # provider only; model comes from settings
tau --effort high                       # raise reasoning effort for this run
```

### Continue later

Sessions save automatically to `~/.tau/sessions/`, organized by working directory:

```bash
tau --resume                       # continue the most recent session
tau --resume abc123                # resume a specific session by ID
tau --ephemeral                    # do not save this session at all
tau --name "release audit"         # set the session display name at startup
```

Inside a session, use `/resume`, `/new`, `/tree`, `/fork`, and `/clone` to manage sessions. See [Sessions](sessions.md).

### Non-interactive mode

For one-shot prompts:

```bash
tau --print "Summarize this codebase"          # print the reply and exit
cat README.md | tau --print "Summarize this"   # piped stdin is merged into the prompt
tau --prompt "Audit this repo" -f json         # structured JSON event stream
```

Piped stdin, `@file` contents, and the explicit prompt are combined in that order. Use `--mode rpc` for bidirectional process integration — see [CLI Reference](cli-reference.md).

## Give Tau Project Instructions

Tau loads context files at startup so the agent has standing instructions. Add an `AGENTS.md` (or `CLAUDE.md`) to your project:

```markdown
# Project Instructions

- This is a Python CLI framework project.
- All code must have type hints.
- Run `pytest` before suggesting changes.
```

Tau walks from the Git repository root down to the current directory, loading at most one context file per directory and preferring `AGENTS.md` over `CLAUDE.md`. Files closer to the current directory take precedence. Outside a Git repository it checks only the current directory.

Run `/reload` after editing a context file, or disable discovery entirely with `--no-context-files`.

## Next Steps

- [Usage Guide](usage.md) — interactive mode, slash commands, and sessions
- [CLI Reference](cli-reference.md) — every flag, subcommand, and run mode
- [Installation](installation.md) — credential precedence and troubleshooting
- [Settings](settings.md) — persistent configuration
- [Keybindings](keybindings.md) — shortcuts and customization
- [Extensions](extensions.md) — add custom tools and commands
