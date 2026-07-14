# Installation

This page covers installation and authentication setup for Tau and its inference providers.

## Prerequisites

- Python 3.12 or higher
- pip, uv, or another Python package manager
- An API key or subscription from at least one inference provider

## Install Tau

### From PyPI

```bash
pip install tau-coding-agent
```

### From Source

Clone the repository and install in editable mode:

```bash
git clone https://github.com/Jeomon/Tau.git
cd Tau
pip install -e .
```

### Verify Installation

Check that Tau is installed:

```bash
tau --version
tau --help
```

## Inference Provider Setup

Tau supports multiple LLM providers. Each requires API credentials.

### Anthropic

Set the environment variable:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

To obtain an API key, visit [Anthropic's console](https://console.anthropic.com) and create a new key.

### OpenAI

```bash
export OPENAI_API_KEY=sk-...
```

Get your API key from [OpenAI's platform](https://platform.openai.com/account/api-keys).

### Google Gemini

```bash
export GOOGLE_API_KEY=...
tau --model google/gemini-2.5-flash
```

Create a key at [Google AI Studio](https://aistudio.google.com). The `google`
provider uses the Gemini Developer API and is separate from the Google Vertex
AI provider.

### Mistral AI

```bash
export MISTRAL_API_KEY=...
```

Get your key from [Mistral's console](https://console.mistral.ai).

### Ollama (Local)

Ollama uses `http://localhost:11434` by default and does not require an API
key. Configure a different endpoint through a provider extension.

## Configure Authentication

### Environment Variables

The simplest method. Set provider keys as environment variables before launching Tau:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
tau
```

### Configuration File

Store credentials in `~/.tau/auth.json`:

```json
{
  "anthropic": { "type": "api_key", "key": "sk-ant-..." },
  "openai": { "type": "api_key", "key": "sk-..." },
  "google": { "type": "api_key", "key": "..." }
}
```

The file is created with restricted permissions (`0600`). Credentials in the auth file take priority over environment variables.

### Key Resolution

The `key` field supports:

- **Literal values**: directly used
- **Environment variables**: `"$MY_KEY"` or `"${MY_KEY}"`
- **Shell commands**: `"!security find-generic-password -ws 'anthropic'"` (executed once, cached for the process lifetime)

Examples:

```json
{
  "anthropic": { "type": "api_key", "key": "$ANTHROPIC_API_KEY" },
  "openai": { "type": "api_key", "key": "!op read 'op://vault/item/key'" }
}
```

## Test Your Setup

Test with a simple one-shot prompt to verify credentials work:

```bash
tau --print "Say exactly: hello"
```

This runs Tau once, sends a prompt, prints the response, and exits. If you see a response, authentication is working.

For interactive mode, just run:

```bash
tau
```

When you start Tau, it will load your models. Press `/model` to see all available models for your configured providers.

## Uninstall

To remove Tau:

```bash
pip uninstall tau-coding-agent
```

This removes the tau command but leaves configuration and session data in `~/.tau/`.

## Troubleshooting

Run `tau doctor` first — it checks settings/auth file integrity, credential
status (including whether stored OAuth tokens are still valid), model/provider
resolution, extensions, session storage, logs, and installed packages in one
pass. Add `--fix` to auto-repair the safe, reversible cases (expired OAuth
tokens, dangling extension entries, corrupt session files).

```bash
tau doctor
tau doctor --fix
```

### No Models Found

Check that your API key is set correctly:

```bash
env | grep -i "api_key\|key"
```

Verify the key matches your provider's requirements. Some providers (e.g., Anthropic) have specific key formats.

### Provider Connection Errors

If Tau cannot connect to a provider, check:

1. **Network connectivity**: Can you reach the provider's endpoint?
2. **API key validity**: Is your key expired or revoked?
3. **Regional restrictions**: Is your location or IP blocked by the provider?

### Ollama Connection Issues

If using Ollama, ensure the service is running:

```bash
ollama serve
```

Tau currently expects the service at `http://localhost:11434`.

### `uv tool install` fails building `pyxclip` on Python 3.14

`pyxclip` (used for clipboard support) ships prebuilt wheels only up to
Python 3.13 — its `pyo3` bindings don't yet support 3.14. `uv tool install`
picks uv's newest managed Python by default (currently 3.14) regardless of
this project's `requires-python` bound, so the install falls back to a source
build that fails with an error like:

```
error: The configured Python interpreter version (3.14) is newer than PyO3's maximum supported version (3.13)
```

Work around it by pinning the interpreter for the install:

```bash
uv tool install . --python 3.13
```

Or set it once for your shell so future installs don't need the flag:

```bash
# macOS/Linux
export UV_PYTHON=3.13

# Windows (PowerShell), current session
$env:UV_PYTHON = "3.13"

# Windows (PowerShell), persisted for future sessions
[Environment]::SetEnvironmentVariable("UV_PYTHON", "3.13", "User")
```

## Next Steps

- [Quickstart](quickstart.md) - Run your first session
- [Inference Providers](inference-providers.md) - Detailed provider reference
- [Settings](settings.md) - Configuration options
