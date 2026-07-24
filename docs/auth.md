# Authentication

Tau stores provider credentials in `~/.tau/auth.json`. This page covers where
credentials come from, the order they are resolved in, the file format, and the
commands that manage them.

## Table of Contents

- [Credential Resolution Order](#credential-resolution-order)
- [Credential Types](#credential-types)
- [Credential References](#credential-references)
- [Auth File](#auth-file)
- [Environment Variables](#environment-variables)
- [Managing Credentials](#managing-credentials)
- [OAuth Providers](#oauth-providers)
- [Checking Auth Status](#checking-auth-status)
- [Programmatic Access](#programmatic-access)
- [Security](#security)

## Credential Resolution Order

`AuthManager.get_api_key(provider)` resolves in this order:

1. **Runtime override**: set programmatically with
   `auth_manager.set_runtime_api_key(provider, key)`. Never persisted.
2. **Stored credential**: the `~/.tau/auth.json` entry for that provider id.
   - An `api_key` credential resolves its `key` (which may be a reference).
   - An `oauth` credential is refreshed if expired, then its access token is
     used. If no OAuth provider is registered under that id, or the refresh
     fails, resolution returns nothing. It does **not** fall through to the
     environment variable.
3. **Environment variable**: `<PROVIDER>_API_KEY`, reached only when no
   credential is stored at all.

The first source with a value wins. A stored credential deliberately blocks the
environment fallback, so a broken stored entry surfaces as an auth error rather
than silently switching to a different key.

> `<PROVIDER>` is the provider id upper-cased with no other transformation.
> Hyphenated ids therefore have no usable env var: `google-vertex` would map to
> `GOOGLE-VERTEX_API_KEY`. Those providers use ambient cloud credentials or
> OAuth instead. See [Environment Variables](#environment-variables).

## Credential Types

### API Key

A static string used as the bearer token:

```json
{
  "anthropic": {
    "type": "api_key",
    "key": "sk-ant-..."
  }
}
```

### OAuth

Used by subscription providers. `expires` is a Unix timestamp in
**milliseconds**:

```json
{
  "github-copilot": {
    "type": "oauth",
    "access": "ghu_...",
    "refresh": "ghr_...",
    "expires": 1718000000000,
    "extra": {}
  }
}
```

`extra` holds provider-specific values (endpoints, account ids). On refresh the
old `extra` is merged under the new one, so values the refresh response omits
are preserved.

Tokens refresh automatically. A token is considered expired 30 seconds before
its actual expiry, so it cannot lapse mid-request. Refreshes run under an
async file lock, and the lock holder re-checks expiry before refreshing. If
another Tau instance already refreshed, its result is adopted instead of
rotating the refresh token a second time.

If a request fails with an auth error even though the token is not yet expired
(revoked or rotated server-side), Tau force-refreshes once and retries for free.
A **transient** refresh failure leaves the credential in place for a later
retry; an **unrecoverable** one (`invalid_grant`, `invalid_request`,
`invalid_token`, or HTTP 400/401/403) deletes the stored credential so you are
prompted to log in again.

| `type` | Fields |
|--------|--------|
| `api_key` | `key` |
| `oauth` | `access`, `refresh`, `expires`, `extra` |

Entries with any other `type` are ignored when the file is parsed.

## Credential References

A `key` may be a literal or a reference resolved at runtime, which keeps the
secret out of `auth.json`:

| `key` value | Resolves to |
|-------------|-------------|
| `"sk-ant-..."` | The literal string |
| `"$ANTHROPIC_API_KEY"` | The named environment variable (empty string if unset) |
| `"!op read op://vault/anthropic/key"` | The command's trimmed stdout, run in a shell |

Only the bare `$NAME` form is supported. There is no `${NAME}` syntax, and
references are not interpolated inside longer strings.

Resolution is **memoized for the process lifetime**, so a `!command` runs only
the first time the key is needed, not per request. Failed resolutions (empty
result) are not cached, so fixing the environment or the command and reloading
re-resolves instead of being stuck on the empty value.

The same syntax works anywhere a secret is entered: `/login`, `auth.json`,
`tau auth set`, custom request headers, proxy URL and proxy headers, and
extension settings.

## Auth File

`~/.tau/auth.json` is a flat JSON object keyed by provider id:

```json
{
  "anthropic": { "type": "api_key", "key": "sk-ant-..." },
  "openai":    { "type": "api_key", "key": "$OPENAI_API_KEY" },
  "groq":      { "type": "api_key", "key": "!op read op://vault/groq/key" },
  "github-copilot": {
    "type": "oauth",
    "access": "ghu_...",
    "refresh": "ghr_...",
    "expires": 1718000000000,
    "extra": {}
  }
}
```

| Path | Mode | Notes |
|------|------|-------|
| `~/.tau/` | `0700` | Created on first use |
| `~/.tau/auth.json` | `0600` | Created containing `{}`; re-chmodded after every write |
| `~/.tau/auth.lock` | — | Lock file guarding read-modify-write cycles |

Writes are atomic: the whole file is re-serialized under the lock and replaced,
so a crash mid-write cannot truncate it. Reads take the lock too, so a
concurrent OAuth refresh in another Tau instance is never observed half-written.

Storage is pluggable. `FileAuthStorage` backs the file above; `InMemoryAuthStorage`
is used by `AuthManager.in_memory()` for tests and embedding hosts that must not
touch disk.

## Environment Variables

Any provider whose id contains no hyphen picks up `<PROVIDER>_API_KEY`
automatically, with no file needed:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GROQ_API_KEY="gsk_..."
tau
```

| Provider id | Env var |
|-------------|---------|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `google` | `GOOGLE_API_KEY` |
| `mistral` | `MISTRAL_API_KEY` |
| `groq` | `GROQ_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `cerebras` | `CEREBRAS_API_KEY` |
| `fireworks` | `FIREWORKS_API_KEY` |
| `huggingface` | `HUGGINGFACE_API_KEY` |
| `nvidia` | `NVIDIA_API_KEY` |
| `perplexity` | `PERPLEXITY_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `bedrock` | `BEDROCK_API_KEY` |
| `kimi` | `KIMI_API_KEY` |
| `minimax` | `MINIMAX_API_KEY` |
| `zai` | `ZAI_API_KEY` |
| `kilocode` | `KILOCODE_API_KEY` |
| `subconscious` | `SUBCONSCIOUS_API_KEY` |
| `tinker` | `TINKER_API_KEY` |
| `fal` | `FAL_API_KEY` |
| `elevenlabs` | `ELEVENLABS_API_KEY` |
| `sarvam` | `SARVAM_API_KEY` |
| `together` | `TOGETHER_API_KEY` |

Providers requiring **no** credential (`AuthType.None_`): `ollama`, `lmstudio`,
`vllm`, `llamacpp`, `google-vertex`, `anthropic-vertex`, `openai-vertex`.

The Vertex providers read Google Cloud's own ambient environment instead. See
[Inference Providers](inference-providers.md#google-cloud-vertex-ai).

## Managing Credentials

### `tau auth` (non-interactive)

| Command | Description |
|---------|-------------|
| `tau auth list` | List stored credentials with masked keys |
| `tau auth status` | Show every known provider with type, source, and configured state |
| `tau auth set <PROVIDER> <KEY>` | Store an API key (literal, `$ENV_VAR`, or `!command`) |
| `tau auth unset <PROVIDER>` | Remove a provider's stored credential |
| `tau auth login <PROVIDER>` | Run an OAuth flow for an OAuth-capable provider |
| `tau auth logout <PROVIDER>` | Revoke and remove an OAuth credential |

```bash
tau auth set anthropic sk-ant-...                        # literal key
tau auth set groq '!op read op://vault/groq/credential'  # 1Password reference
tau auth login github-copilot                            # OAuth flow
tau auth status                                          # what is configured
```

`tau auth status` output:

```text
  Provider                 Type     Source   Status
  ──────────────────────────────────────────────────────
  openai                   api_key  env      ✓ configured
  anthropic                api_key  stored   ✓ configured
  groq                     api_key  —        ✗ not configured
  github-copilot           oauth    stored   ✓ configured
```

There is **no `--api-key` flag** on the main `tau` command. Use `tau auth set`,
an environment variable, or a runtime override from an embedding application.

### `/login` (interactive)

Run `/login` inside a session. Tau first asks for the authentication type:

- **Subscription**: the OAuth provider list. Tau opens your browser and prompts
  for any required input (device code, redirect URL) inside the TUI.
- **API key**: the API-key provider list. Select one and enter the key into a
  masked input. Literals, `$ENV_VAR`, and `!command` are all accepted;
  references are stored verbatim and resolved at runtime.

Either way the credential is written to `~/.tau/auth.json`.

### `/logout` (interactive)

Run `/logout` to pick from the providers that have credentials stored in
`~/.tau/auth.json`. For OAuth providers Tau calls the provider's revocation
flow before removing the entry. Environment variables and runtime overrides are
unaffected.

### Editing the file directly

Edit `~/.tau/auth.json` by hand and keep the shape shown in
[Auth File](#auth-file). Tau reloads it before listing available models, and any
write it makes re-chmods the file to `0600`.

## OAuth Providers

| Provider id | Display name | Flow |
|-------------|--------------|------|
| `openai-codex` | ChatGPT Plus/Pro (Codex Subscription) | Local callback server |
| `anthropic-claude-code` | Anthropic (Claude Pro/Max) | Local callback server |
| `github-copilot` | GitHub Copilot | Device code |
| `google-antigravity` | Google Antigravity | Local callback server |
| `xai-grok` | xAI Grok CLI (SuperGrok Subscription) | Local callback server |

These ids are the `auth.json` keys and the arguments to
`tau auth login` / `tau auth logout`.

An OAuth provider is only selectable when a matching OAuth credential is
stored. During model resolution, OAuth providers without credentials are
skipped in favor of the next registered variant of the same model, which is
how a model available both by subscription and by API key falls back cleanly.

## Checking Auth Status

`AuthManager.get_auth_status(provider)` returns an `AuthStatus`:

| Field | Type | Description |
|-------|------|-------------|
| `configured` | `bool` | Whether any source has a value |
| `source` | `"stored" \| "runtime" \| "env" \| None` | Which source matched |
| `label` | `str \| None` | The env var name, or `"--api-key"` for a runtime override |

> `get_auth_status` checks **stored** before **runtime**, whereas
> `get_api_key` prefers **runtime**. With both set, the status reports
> `"stored"` while requests use the runtime override.

## Programmatic Access

```python
import asyncio

from tau.auth.manager import AuthManager
from tau.inference.provider.registry import ProviderRegistry


async def main() -> None:
    registry = ProviderRegistry.from_builtins()
    auth = AuthManager.create(registry)          # ~/.tau/auth.json

    status = auth.get_auth_status("anthropic")
    print(status.configured, status.source, status.label)

    print(auth.list())                            # providers with stored credentials
    print(auth.has("anthropic"), auth.is_oauth("github-copilot"))

    # Ephemeral override — highest priority, never written to disk
    auth.set_runtime_api_key("anthropic", "sk-ant-...")
    key = await auth.get_api_key("anthropic")     # refreshes OAuth if needed
    print(bool(key))
    auth.remove_runtime_api_key("anthropic")

    for error in auth.drain_errors():             # storage/refresh errors, for status UI
        print("auth error:", error)


asyncio.run(main())
```

Constructors:

| Constructor | Storage |
|-------------|---------|
| `AuthManager.create(registry, auth_path=None)` | `FileAuthStorage`, defaults to `~/.tau/auth.json` |
| `AuthManager.from_storage(registry, storage)` | Any `AuthStorage` implementation |
| `AuthManager.in_memory(registry, initial=None)` | `InMemoryAuthStorage`, seeded from a dict |

Mutators (`set`, `remove`) write through to storage immediately. `reload()`
re-reads the file. Storage failures are recorded rather than raised; drain them
with `drain_errors()`.

## Security

- `~/.tau/auth.json` is `0600`, `~/.tau/` is `0700`, re-applied on every write.
- Credentials are never logged and never written into session files.
- OAuth refresh tokens are written atomically under a file lock.
- `tau auth list` masks stored keys; `tau auth status` never prints values.
- To keep secrets off disk entirely, store a `$ENV_VAR` or `!command` reference
  (only the reference is written; the value is fetched at runtime).
- A dead refresh token causes the stored credential to be deleted, so a stale
  token is not retried indefinitely.

## Next Steps

- [Inference Providers](inference-providers.md): every provider and its setup
- [Inference](inference.md): how credentials reach a request
- [Settings](settings.md): model and provider defaults
- [Installation](installation.md): first-time setup
