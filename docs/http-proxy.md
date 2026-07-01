# HTTP Proxy Support

Tau supports HTTP proxy configuration for all HTTP/HTTPS traffic via **settings.json** or **environment variables**. This is essential for corporate environments, restricted networks, and API gateway routing.

## Use Cases

### Corporate and Enterprise Environments
Many organizations require all HTTP/HTTPS traffic to route through corporate proxies for security monitoring, compliance, and access control. The `httpProxy` setting allows tau to work in these restricted environments without manual environment variable configuration.

### Network Restrictions
Some networks block direct access to external LLM provider APIs (Anthropic, OpenAI, Google, Bedrock, etc.). Proxy support enables routing through approved servers that have whitelist access.

### API Gateways and Custom Endpoints
Proxies enable routing through API gateways that provide rate limiting, authentication, logging, and request transformation. This is particularly relevant for Amazon Bedrock, which supports custom VPC proxy endpoints for secure internal network access.

## Configuration Priority

1. **settings.json** (highest priority) — `http_proxy` section
2. **Environment variables** (fallback) — standard proxy env vars
3. **No proxy** (default)

## Settings Configuration (Recommended)

Configure HTTP proxy in `~/.tau/settings.json` or `.tau/settings.json`. **Note:** HTTP proxy settings are configured via JSON only (not available in the interactive `/settings` command) since they support custom headers for authentication.

```json
{
  "httpProxy": {
    "url": "http://proxy.example.com:8080",
    "noProxy": "localhost,127.0.0.1,*.internal.example.com",
    "headers": {
      "Proxy-Authorization": "Bearer token123",
      "X-Custom-Header": "value"
    }
  }
}
```

**Fields:**
- `url` (required) — Proxy URL for both HTTP and HTTPS requests
- `noProxy` (optional) — Comma-separated hosts to bypass the proxy
- `headers` (optional) — Custom headers for proxy authentication

Settings here override corresponding environment variables (HTTP_PROXY, HTTPS_PROXY, NO_PROXY).

### Custom Proxy Headers

Some corporate proxies require custom authentication headers:

```json
{
  "httpProxy": {
    "url": "http://proxy.example.com:8080",
    "headers": {
      "Proxy-Authorization": "Basic dXNlcm5hbWU6cGFzc3dvcmQ=",
      "X-Proxy-Token": "secret-token"
    }
  }
}
```

Alternatively, embed credentials directly in the proxy URL:

```json
{
  "httpProxy": {
    "url": "http://username:password@proxy.example.com:8080"
  }
}
```

## Environment Variables

Tau respects the following proxy environment variables (case-insensitive):

| Variable | Purpose |
|----------|---------|
| `HTTP_PROXY` / `http_proxy` | Proxy for HTTP requests |
| `HTTPS_PROXY` / `https_proxy` | Proxy for HTTPS requests |
| `ALL_PROXY` / `all_proxy` | Fallback proxy for all requests |
| `NO_PROXY` / `no_proxy` | Comma/space-separated list of hosts to exclude from proxying |
| `npm_config_http_proxy` | npm config variant for HTTP proxy |
| `npm_config_https_proxy` | npm config variant for HTTPS proxy |
| `npm_config_proxy` | npm config variant for fallback proxy |

## Quick Start

Set environment variables before running tau:

```bash
# Using an HTTP proxy
export HTTPS_PROXY=http://proxy.example.com:8080
tau --help

# Exclude internal hosts from proxying
export NO_PROXY=*.internal.example.com,localhost
tau --help

# Windows PowerShell
$env:HTTPS_PROXY="http://proxy.example.com:8080"
tau --help
```

## Usage in Code

### Basic Usage

```python
from tau.utils.http_proxy import get_proxy_url_for_target

# Get proxy URL (checks settings first, then env vars)
proxy = get_proxy_url_for_target("https://api.anthropic.com", settings_manager)
if proxy:
    print(f"Using proxy: {proxy}")
```

### With httpx Client

```python
import httpx
from tau.utils.http_proxy import get_proxies_for_client, get_proxy_headers

api_base = "https://api.example.com"
proxies = get_proxies_for_client(api_base, settings_manager)
headers = get_proxy_headers(settings_manager)

async with httpx.AsyncClient(proxies=proxies, headers=headers) as client:
    response = await client.get(f"{api_base}/v1/models")
```

With custom proxy headers:

```python
import httpx
from tau.utils.http_proxy import get_proxies_for_client, get_proxy_headers

api_base = "https://api.example.com"
proxies = get_proxies_for_client(api_base, settings_manager)
proxy_headers = get_proxy_headers(settings_manager) or {}

# Merge with any other default headers
headers = {
    "User-Agent": "tau/1.0",
    **proxy_headers,  # Include custom proxy headers
}

async with httpx.AsyncClient(proxies=proxies, headers=headers) as client:
    response = await client.get(f"{api_base}/v1/models")
```

### With Existing httpx Client

```python
import httpx
from tau.utils.http_proxy import get_proxy_url_for_target

api_base = "https://api.example.com"
proxy_url = get_proxy_url_for_target(api_base)

client_kwargs = {}
if proxy_url:
    client_kwargs["proxies"] = {
        "http://": proxy_url,
        "https://": proxy_url,
    }

async with httpx.AsyncClient(**client_kwargs) as client:
    response = await client.get(f"{api_base}/v1/models")
```

## NO_PROXY Exclusions

`NO_PROXY` controls which hosts bypass the proxy:

```bash
# Single host
export NO_PROXY=localhost

# Multiple hosts (comma or space-separated)
export NO_PROXY=localhost, 127.0.0.1, internal.example.com

# Wildcard domains (*.example.com matches sub.example.com)
export NO_PROXY=*.internal.example.com

# Disable proxying entirely
export NO_PROXY=*
```

## Protocol Support

✅ **Supported:**
- `http://` proxy URLs
- `https://` proxy URLs (secure proxy)

❌ **Not Supported:**
- SOCKS proxies (`socks5://`)
- PAC proxies (`pac+http://`)

Attempting to use unsupported proxy types will raise a `ValueError` with a helpful message.

## Proxy URL Format

Proxy URLs can be specified with or without a protocol prefix:

```bash
# With protocol (preferred)
export HTTPS_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=https://secure-proxy.example.com:8443

# Without protocol (will use same protocol as target)
export HTTPS_PROXY=proxy.example.com:8080
```

## Examples

### Corporate Proxy

```bash
export HTTP_PROXY=http://corp-proxy.mycompany.com:3128
export HTTPS_PROXY=http://corp-proxy.mycompany.com:3128
export NO_PROXY=*.mycompany.com,localhost,127.0.0.1
tau
```

### Authenticated Proxy

```bash
# Include credentials in URL (URL-encoded if needed)
export HTTPS_PROXY=http://username:password@proxy.example.com:8080
tau
```

### Secure Proxy Connection

```bash
# Use HTTPS to connect to proxy (more secure)
export HTTPS_PROXY=https://secure-proxy.example.com:8443
tau
```

### Local Development

```bash
# Route through local proxy/gateway for debugging/inspection
export HTTPS_PROXY=http://localhost:8888
export NO_PROXY=*
# Then run with mitmproxy or similar on localhost:8888
tau
```

## Troubleshooting

### Proxy not being used

1. Check that env vars are exported (not just set):
   ```bash
   export HTTPS_PROXY=http://proxy.example.com:8080  # Correct
   HTTPS_PROXY=http://proxy.example.com:8080 tau     # Wrong (not exported)
   ```

2. Verify NO_PROXY isn't excluding the target:
   ```bash
   echo $NO_PROXY  # Check current value
   ```

3. Test proxy connectivity manually:
   ```bash
   curl -v -x http://proxy.example.com:8080 https://api.example.com
   ```

### Proxy connection fails

1. Verify proxy URL is correct (host, port, protocol)
2. Check firewall rules allow outbound to proxy
3. Try with `http://` instead of `https://` (some proxies don't support HTTPS)
4. Check proxy authentication requirements (add credentials to URL if needed)

### "Unsupported proxy protocol"

If you see: `Unsupported proxy protocol. SOCKS and PAC proxy URLs are not supported`

This means your `HTTPS_PROXY` or `ALL_PROXY` env var is set to a SOCKS or PAC URL. Change it to HTTP or HTTPS:

```bash
# Wrong
export HTTPS_PROXY=socks5://proxy.example.com:1080

# Correct
export HTTPS_PROXY=http://proxy.example.com:8080
```

---

## API Reference

### `get_proxy_url_for_target(target_url: str) → Optional[str]`

Get HTTP proxy URL for a specific target from environment variables.

**Args:**
- `target_url`: Target URL (e.g., `"https://api.anthropic.com"`)

**Returns:**
- Proxy URL string (e.g., `"http://proxy.example.com:8080"`) or `None`

**Raises:**
- `ValueError`: If proxy URL uses unsupported protocol (SOCKS, PAC)

**Example:**
```python
from tau.utils.http_proxy import get_proxy_url_for_target

proxy = get_proxy_url_for_target("https://api.example.com")
if proxy:
    print(f"Using: {proxy}")
```

### `get_proxies_for_client(api_base_url: str) → Optional[dict[str, str]]`

Get proxy configuration dict for httpx or requests libraries.

**Args:**
- `api_base_url`: Base URL of the API (e.g., `"https://api.example.com"`)

**Returns:**
- Dict with `"http://"` and `"https://"` keys pointing to proxy URL, or `None`

**Raises:**
- `ValueError`: If proxy URL uses unsupported protocol

**Example:**
```python
from tau.utils.http_proxy import get_proxies_for_client
import httpx

proxies = get_proxies_for_client("https://api.example.com")
async with httpx.AsyncClient(proxies=proxies) as client:
    response = await client.get("...")
```

## Implementation Details

### Startup Application

The HTTP proxy settings are applied during tau initialization, before any HTTP requests are made to LLM providers or external services. This ensures all requests respect the configured proxy.

### Environment Variable Handling

Tau respects existing proxy environment variables and doesn't override them if already set, allowing for:
- Per-session overrides via environment variables
- Per-command overrides via shell exports
- Global configuration via settings.json (takes precedence)

Priority: **settings.json** > **environment variables** > **defaults**

### Proxy Resolution

Tau handles protocol-specific proxies:
- `http_proxy` or `HTTP_PROXY` — used for HTTP requests
- `https_proxy` or `HTTPS_PROXY` — used for HTTPS requests  
- `all_proxy` or `ALL_PROXY` — fallback for all requests
- `no_proxy` or `NO_PROXY` — hostname exclusions (comma/space-separated, supports wildcards)

### OAuth and Authentication

OAuth login/refresh operations (e.g., GitHub Copilot, OpenAI, Anthropic) also respect HTTP proxy settings, ensuring authenticated flows work through corporate proxies.

### Notes

- The `httpProxy` setting is **global-only** and cannot be overridden at the project level
- Different providers may have additional proxy-related configuration options (e.g., Amazon Bedrock VPC endpoints)
- Proxy validation occurs at startup — invalid proxy URLs will raise an error before any requests are made
- SOCKS and PAC proxies are not supported; only HTTP/HTTPS proxies are allowed

---

## See Also

- [Settings](settings.md) — Main settings reference
- [Extensions](extensions.md) — Extensions can also use proxy settings via SettingsManager
- [Inference Providers](inference-providers.md) — Model provider configuration
