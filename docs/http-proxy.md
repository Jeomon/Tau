# HTTP Proxy

Tau routes outbound HTTP/HTTPS traffic through a proxy using the standard proxy
environment variables. A settings-based configuration and a resolver API also
exist for embedding applications and extensions.

> **Read this first:** the built-in inference adapters do **not** call Tau's
> proxy resolver. Proxying works today because every HTTP client Tau creates is
> `httpx`-based, and httpx reads `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` /
> `NO_PROXY` from the environment itself. The `http_proxy` settings block and
> `tau.utils.http_proxy` are a resolver library for code that opts into them —
> they do not currently affect built-in provider requests. See
> [Current Wiring](#current-wiring).

## Table of Contents

- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [NO_PROXY Exclusions](#no_proxy-exclusions)
- [Settings](#settings)
- [Current Wiring](#current-wiring)
- [Resolver API](#resolver-api)
- [TLS and Certificates](#tls-and-certificates)
- [Troubleshooting](#troubleshooting)

## Quick Start

Export the standard variables before running Tau:

```bash
export HTTPS_PROXY=http://proxy.example.com:8080     # proxy for HTTPS targets
export HTTP_PROXY=http://proxy.example.com:8080      # proxy for HTTP targets
export NO_PROXY=localhost,127.0.0.1                  # bypass list
tau
```

An authenticated proxy takes credentials in the URL — this is the only way to
supply proxy credentials to built-in provider requests today:

```bash
export HTTPS_PROXY=http://username:password@proxy.example.com:8080
tau
```

Windows PowerShell:

```powershell
$env:HTTPS_PROXY="http://proxy.example.com:8080"
tau
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `HTTP_PROXY` / `http_proxy` | Proxy for targets with an `http://` scheme |
| `HTTPS_PROXY` / `https_proxy` | Proxy for targets with an `https://` scheme |
| `ALL_PROXY` / `all_proxy` | Fallback when no scheme-specific variable is set |
| `NO_PROXY` / `no_proxy` | Hosts that bypass the proxy |

Lookups are case-insensitive: Tau's resolver checks the lowercase name first,
then the uppercase one. The scheme-specific variable is selected from the
**target** URL's scheme, then `ALL_PROXY` is used as a fallback.

Only `http://` and `https://` proxy URLs are supported. A SOCKS or PAC URL
raises a `ValueError` from Tau's resolver. A proxy value with no `://` prefix is
given the target's scheme.

## NO_PROXY Exclusions

```bash
export NO_PROXY=localhost                         # single host
export NO_PROXY=localhost,127.0.0.1,internal.example.com
export NO_PROXY=*.internal.example.com            # wildcard subdomains
export NO_PROXY=internal.example.com:8443         # host with port
export NO_PROXY=*                                 # bypass the proxy entirely
```

Matching rules in Tau's resolver:

- The list is split on **commas only**. Whitespace around entries is trimmed,
  so `a, b` works, but a space-separated list without commas does not.
- `*` alone disables proxying for every host.
- `*.example.com` matches any host ending in `.example.com`.
- Any other entry must match the hostname exactly.
- An entry of the form `host:port` only applies when the port also matches;
  a non-numeric portion after the colon is treated as part of the hostname.
- Comparison is case-insensitive.

## Settings

`~/.tau/settings.json` (or a project `.tau/settings.json`) accepts an
`http_proxy` block:

```json
{
  "http_proxy": {
    "url": "http://proxy.example.com:8080",
    "no_proxy": "localhost,127.0.0.1,*.internal.example.com",
    "headers": {
      "Proxy-Authorization": "Bearer token123",
      "X-Custom-Header": "value"
    }
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | `string \| null` | `null` | Proxy URL for both HTTP and HTTPS |
| `no_proxy` | `string \| null` | `null` | Comma-separated hosts to bypass |
| `headers` | `object \| null` | `null` | Custom headers for proxy authentication |

`url` and every value in `headers` support the same secret references as API
keys — a literal, `$ENV_VAR`, or `!command`. See
[Authentication](auth.md#credential-references).

```json
{
  "http_proxy": {
    "url": "http://proxy.example.com:8080",
    "headers": {
      "Proxy-Authorization": "!op read op://vault/proxy/token"
    }
  }
}
```

These fields are also editable from the interactive `/settings` command, under
**Proxy Settings** (URL, No-proxy hosts, Headers as a JSON object). Edits made
there are written to global settings.

When Tau's resolver is used, settings take precedence over environment
variables; `http_proxy.no_proxy` replaces `NO_PROXY` rather than merging with it.

## Current Wiring

Understanding what is actually connected matters when a proxy does not take
effect.

| Mechanism | Affects built-in provider requests? |
|-----------|-------------------------------------|
| `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY` env vars | **Yes** — via httpx's own environment handling |
| `http_proxy.url` in settings | No |
| `http_proxy.no_proxy` in settings | No |
| `http_proxy.headers` in settings | No |

Every HTTP client Tau constructs — the direct `httpx` clients used by the media,
Codex, Antigravity, model-catalog, and local-discovery paths, and the httpx
clients inside the OpenAI, Anthropic, Mistral, Ollama, and Google SDKs — is
built with a base URL, credentials, and headers only. None is given a proxy,
`mounts`, or a custom transport, and none disables httpx's environment
handling. So the env vars are honored, and the settings block is not consulted.

Practical consequences:

- Setting only `http_proxy.url` in `settings.json` will not proxy anything.
  Export `HTTPS_PROXY` instead.
- Custom proxy headers such as `Proxy-Authorization` have no path to built-in
  requests. Embed the credentials in the proxy URL instead.
- Tau's richer `NO_PROXY` semantics (`host:port` entries, `*.domain` wildcards)
  apply only to code that calls the resolver; httpx applies its own rules to
  built-in requests.

Extensions and embedding applications that build their own HTTP clients can opt
in with the [Resolver API](#resolver-api).

## Resolver API

`tau.utils.http_proxy` resolves a proxy for a target URL from settings, then
from the environment. It is pure resolution — it never mutates the environment
and never configures a client for you.

### `get_proxy_url_for_target(target_url, settings_manager=None) -> str | None`

Returns the proxy URL for a target, or `None` when the target should be reached
directly. Raises `ValueError` for a SOCKS or PAC proxy URL. Without a
`settings_manager`, only environment variables are consulted.

### `get_proxies_for_client(api_base_url, settings_manager=None) -> dict[str, str] | None`

Returns `{"http://": url, "https://": url}` for building httpx mounts, or
`None` when no proxy applies.

### `get_proxy_headers(settings_manager=None) -> dict[str, str] | None`

Returns the configured proxy headers with secret references resolved. Requires a
`settings_manager`; returns `None` without one.

### Example

```python
"""Build an httpx client that honors Tau's proxy settings."""

import asyncio
from pathlib import Path

import httpx

from tau.settings.manager import SettingsManager
from tau.utils.http_proxy import get_proxies_for_client, get_proxy_headers
from tau.utils.ssl_context import get_shared_ssl_context


async def main() -> None:
    api_base = "https://api.example.com"
    settings = SettingsManager.create(Path.cwd())

    proxies = get_proxies_for_client(api_base, settings)
    mounts = (
        {scheme: httpx.AsyncHTTPTransport(proxy=url) for scheme, url in proxies.items()}
        if proxies
        else None
    )
    headers = {
        "User-Agent": "tau",
        **(get_proxy_headers(settings) or {}),
    }

    async with httpx.AsyncClient(
        mounts=mounts,
        headers=headers,
        verify=get_shared_ssl_context(),
    ) as client:
        response = await client.get(f"{api_base}/v1/models")
        print(response.status_code)


asyncio.run(main())
```

Checking a single target:

```python
from tau.utils.http_proxy import get_proxy_url_for_target

proxy = get_proxy_url_for_target("https://api.anthropic.com")
print(f"via {proxy}" if proxy else "direct")
```

## TLS and Certificates

Tau builds one SSL context per process — `httpx.create_ssl_context()` behind an
`lru_cache` in `tau.utils.ssl_context.get_shared_ssl_context()` — and passes it
to every httpx client it creates directly. This avoids re-parsing the CA bundle
off disk for each of the many short-lived clients Tau opens.

| Property | Behavior |
|----------|----------|
| Certificate verification | Always on. There is no setting or flag to disable it |
| Custom CA bundle | Only whatever OpenSSL picks up from `SSL_CERT_FILE` / `SSL_CERT_DIR`; Tau reads no CA-bundle variables of its own |
| Timing | The context is built once per process, so environment changes after the first HTTPS request have no effect |

A TLS-intercepting corporate proxy therefore needs its CA in the system trust
store, or exported through `SSL_CERT_FILE` before Tau starts.

## Troubleshooting

### Proxy is not being used

1. Confirm the variables are **exported**, not just set for one command:

   ```bash
   export HTTPS_PROXY=http://proxy.example.com:8080   # correct
   HTTPS_PROXY=http://proxy.example.com:8080 tau      # also works, but only for that run
   ```

2. Confirm you are not relying on `settings.json` alone — see
   [Current Wiring](#current-wiring). Export `HTTPS_PROXY`.

3. Check `NO_PROXY` is not excluding the target:

   ```bash
   echo "$NO_PROXY"
   ```

4. Test the proxy directly:

   ```bash
   curl -v -x http://proxy.example.com:8080 https://api.anthropic.com/v1/models
   ```

### Proxy connection fails

1. Verify host, port, and scheme.
2. Check outbound firewall rules to the proxy.
3. Try `http://` to reach the proxy rather than `https://`; many proxies do not
   terminate TLS on their listener.
4. For an authenticated proxy, put the credentials in the URL and URL-encode any
   reserved characters in the password.

### Certificate verification fails

The proxy is likely intercepting TLS. Install its CA in the system trust store,
or point OpenSSL at it before launching Tau:

```bash
export SSL_CERT_FILE=/path/to/corp-ca-bundle.pem
tau
```

### "Unsupported proxy protocol"

Tau's resolver accepts only `http://` and `https://`:

```bash
export HTTPS_PROXY=socks5://proxy.example.com:1080   # rejected
export HTTPS_PROXY=http://proxy.example.com:8080     # supported
```

SOCKS and PAC proxies are not supported.

## See Also

- [Settings](settings.md) — the full settings reference
- [Inference Providers](inference-providers.md) — provider endpoints to allowlist
- [Authentication](auth.md) — `$ENV_VAR` and `!command` secret references
- [Extensions](extensions.md) — extensions can use the resolver via `SettingsManager`
