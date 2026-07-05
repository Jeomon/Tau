# Quickstart

This page gets you from install to a working first tau session in five minutes.

## Install

Tau requires Python 3.12 or higher. Clone the repository and install:

```bash
cd /path/to/tau
pip install -e .
```

The `-e` flag installs tau in editable mode, allowing you to modify the code and see changes immediately.

## Set Up Authentication

Tau supports multiple inference providers. Choose one and set its API key:

### Anthropic

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### OpenAI

```bash
export OPENAI_API_KEY=sk-...
```

### Google Gemini

```bash
export GOOGLE_API_KEY=...
tau --model google/gemini-2.5-flash
```

### Other Providers

See [Inference Providers](inference-providers.md) for all supported providers and environment variables.

## First Session

Run tau in a project directory:

```bash
cd /path/to/your/project
tau
```

You will be prompted to select a model from your configured providers. By default, tau gives the agent these tools:

- `read` - read files
- `write` - create or overwrite files
- `edit` - patch files
- `terminal` - run shell commands
- `glob` - find files by pattern
- `grep` - search file contents
- `ls` - list directory contents

Type a prompt and press Enter:

```text
Summarize this repository and tell me how to run its checks.
```

The agent will read files, run commands, and respond with a summary.

Markdown responses support terminal-readable inline and display LaTeX math.
Long-running terminal tools update their existing output block while streaming
instead of printing a new block for every chunk.

### Media Support

Tau can process multiple file types. Reference them with `@` during input:

- **Images** — `.jpg`, `.png`, `.gif`, `.webp`
- **Audio** — `.mp3`, `.wav`, `.m4a`, `.ogg`
- **Video** — `.mp4`, `.webm`, `.mov`, `.mkv`
- **Text** — `.py`, `.js`, `.md`, `.txt`, and more

You can also paste files directly or drag them from your clipboard.

## Common Things to Try

### Reference Files

Type `@` while typing your prompt to fuzzy-search project files and insert them:

```bash
cd /path/to/your/project
tau
# Then in the editor, type @ to open file picker
```

In non-interactive mode, attach files with `--file PATH`:

```bash
tau --print --prompt "Explain this file" --file src/main.py
```

### Continue a Past Session

Sessions are saved automatically to `~/.tau/sessions/`. Resume the most recent:

```bash
tau --resume
```

Inside a running session, `/resume` opens the interactive session browser.
Use `/tree` to navigate the current session history.

### Use a Different Model

During a session, press `/` to open the command palette, then select `/model` to change models.

Or set a model when starting:

```bash
tau --model claude-sonnet-4-6
tau --model openai/gpt-4o
```

### One-Shot Mode

For a single prompt without opening the TUI:

```bash
tau --print "Summarize this repository"
```

With a file reference:

```bash
tau --print --prompt "Explain this file" --file README.md
```

## Next Steps

- [Installation](installation.md) - Detailed provider setup and configuration
- [Usage Guide](usage.md) - Interactive mode, slash commands, and features
- [Settings](settings.md) - Global and project configuration options
- [Inference Providers](inference-providers.md) - All supported providers
