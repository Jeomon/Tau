# Authentication

Tau stores credentials for LLM providers in `~/.tau/auth.json`. This page explains how credentials are resolved, how to configure them, and the credential types supported.

## Credential Resolution Order

For each provider, tau resolves credentials in this order:

1. **Runtime override** — set programmatically through `AuthManager` (not persisted)
2. **Stored credential** — read from `~/.tau/auth.json`
3. **Environment variable** — `{PROVIDER}_API_KEY` (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)

The first source that has a value wins. If none are found, requests to that provider will fail.

## Credential Types

### API Key

The most common type. A static string passed as a bearer token:

```json
{
  "anthropic": {
    "type": "api_key",
    "key": "sk-ant-..."
  }
}
```

The `key` may also be a **reference** that is resolved at runtime instead of a
literal — useful for keeping the secret out of `auth.json`:

| `key` value | Resolves to |
| --- | --- |
| `"sk-ant-..."` | the literal string |
| `"$ANTHROPIC_API_KEY"` | the named environment variable |
| `"!op read op://vault/anthropic/key"` | the command's stdout (run in a shell) |

References are resolved **once and cached in memory** for the life of the
process, so a `!command` is executed only the first time the key is needed — not
on every request. This works anywhere a key is entered: `/login`, `auth.json`,
extension settings (e.g. web search), and proxy URL / headers.

### OAuth

Used by providers that support OAuth 2.0 flows (e.g. GitHub Copilot):

```json
{
  "copilot": {
    "type": "oauth",
    "access": "ghu_...",
    "refresh": "ghr_...",
    "expires": 1718000000000
  }
}
```

OAuth tokens are refreshed automatically when they expire. Refreshes use a file lock to prevent race conditions between multiple tau instances.

## Configuring Credentials

### Environment variable (recommended for CI/scripts)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
```

No file is needed — tau picks these up automatically.

### Runtime override

Embedding applications may call
`auth_manager.set_runtime_api_key(provider, key)`. The main Tau CLI does not
provide an `--api-key` flag.

### Auth file (direct edit)

Edit `~/.tau/auth.json` directly. The file is created with mode `0600` (owner read/write only). The parent directory is created with mode `0700`.

### `/login` command (interactive, inside the TUI)

Run `/login` from within a session. Tau asks which authentication type to use:

**Subscription (OAuth)** — for providers like GitHub Copilot and OpenAI Codex. Tau opens your browser automatically, then prompts for any required input (device code, redirect URL) inside the TUI. The credential is saved to `~/.tau/auth.json` on success.

**API key** — shows a list of API-key providers. Select one and enter the key into the secure input field (displayed as `***`). You can enter a literal key, a `$ENV_VAR`, or a `!command` (see [API Key](#api-key) above); references are stored verbatim and resolved at runtime. The entry is saved to `~/.tau/auth.json`.

### `/logout` command

Run `/logout` to open a list of providers that have credentials stored in
`~/.tau/auth.json`. Select one to remove it. Environment variables and
programmatic runtime overrides are unaffected.

## Auth File Format

`~/.tau/auth.json` is a JSON object keyed by provider ID:

```json
{
  "anthropic": {
    "type": "api_key",
    "key": "sk-ant-..."
  },
  "openai": {
    "type": "api_key",
    "key": "sk-..."
  }
}
```

Valid `type` values: `"api_key"`, `"oauth"`.

## Checking Auth Status

From Python (e.g. in an extension):

```python
status = auth_manager.get_auth_status("anthropic")
# status.configured  → True / False
# status.source      → "stored" | "runtime" | "env" | None
# status.label       → env var name or "runtime override"
```

## Security

- `~/.tau/auth.json` is created `0600` (read/write for owner only)
- `~/.tau/` is created `0700`
- Credentials are never logged or included in session files
- OAuth refresh tokens are written back atomically under a file lock
- To keep the secret out of `auth.json` entirely, store a `$ENV_VAR` or
  `!command` reference instead of the literal key — only the reference is
  written to disk; the value is fetched at runtime

## Provider IDs

The provider ID used as the key in `auth.json` and the `{PROVIDER}_API_KEY` env var name is the lowercase provider identifier:

| Provider | ID | Env var |
|----------|-----|---------|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Google | `google` | `GOOGLE_API_KEY` |
| Mistral | `mistral` | `MISTRAL_API_KEY` |
| Fireworks AI | `fireworks` | `FIREWORKS_API_KEY` |
| Hugging Face | `huggingface` | `HUGGINGFACE_API_KEY` |
| Subconscious | `subconscious` | `SUBCONSCIOUS_API_KEY` |
| Ollama | `ollama` | None required (local server, default `http://localhost:11434`) |

## Next Steps

- [Inference Providers](inference-providers.md) — Supported providers and models
- [Installation](installation.md) — First-time setup
- [Settings](settings.md) — Provider and model configuration
