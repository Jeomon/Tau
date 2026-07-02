"""HTTP proxy resolution from settings or environment variables.

Supports proxy configuration from:
1. settings.json (http_proxy.http_proxy, http_proxy.https_proxy, http_proxy.no_proxy)
2. Environment variables: HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY (and lowercase variants)
3. npm config variants: npm_config_http_proxy, npm_config_https_proxy, etc.

Settings.json takes precedence over environment variables.

Example:
    from tau.utils.http_proxy import get_proxy_url_for_target

    proxy_url = get_proxy_url_for_target("https://api.anthropic.com")
    if proxy_url:
        print(f"Using proxy: {proxy_url}")
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from tau.settings.manager import SettingsManager

UNSUPPORTED_PROXY_PROTOCOL_MESSAGE = (
    "Unsupported proxy protocol. SOCKS and PAC proxy URLs are not supported; "
    "use an HTTP or HTTPS proxy URL."
)

DEFAULT_PROXY_PORTS = {
    "ftp": 21,
    "gopher": 70,
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
}


def _get_proxy_env(key: str) -> str:
    """Get proxy environment variable (case-insensitive)."""
    return os.environ.get(key.lower(), "") or os.environ.get(key.upper(), "")


def _parse_proxy_target_url(target_url: str) -> tuple[str, str, int] | None:
    """Parse target URL into (scheme, hostname, port) or None if invalid."""
    try:
        parsed = urlparse(target_url)
        if not parsed.scheme or not parsed.hostname:
            return None
        scheme = parsed.scheme
        hostname = parsed.hostname
        port = parsed.port or DEFAULT_PROXY_PORTS.get(scheme, 0)
        return (scheme, hostname, port)
    except Exception:
        return None


def _should_proxy_hostname(hostname: str, port: int, no_proxy: str | None = None) -> bool:
    """Check if hostname should be proxied (respecting NO_PROXY).

    Args:
        hostname: Hostname to check
        port: Port number
        no_proxy: Optional NO_PROXY string. If not provided, checks environment variables.
    """
    no_proxy = _get_proxy_env("no_proxy").lower() if no_proxy is None else no_proxy.lower()

    if not no_proxy:
        return True
    if no_proxy == "*":
        return False

    # NO_PROXY is comma or space-separated list of hosts/patterns to exclude
    for entry in no_proxy.split(","):
        entry = entry.strip()
        if not entry:
            continue

        # Parse host:port format
        if ":" in entry:
            proxy_host, proxy_port_str = entry.rsplit(":", 1)
            try:
                proxy_port = int(proxy_port_str)
                if proxy_port != port:
                    continue  # Port mismatch, check next entry
            except ValueError:
                proxy_host = entry
                proxy_port = None
        else:
            proxy_host = entry
            proxy_port = None

        # Match hostname (supports wildcards like *.example.com)
        if proxy_host.startswith("*."):
            # Wildcard match: *.example.com matches sub.example.com
            if hostname.endswith(proxy_host[1:]):  # Remove leading * and keep .
                return False
        elif proxy_host == hostname:
            return False

    return True


def get_proxy_url_for_target(
    target_url: str, settings_manager: SettingsManager | None = None
) -> str | None:
    """
    Get HTTP proxy URL for a target URL from settings or environment variables.

    Checks in order:
    1. Settings (from settings.json http_proxy.url) - takes precedence
    2. Environment variables (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, etc.)

    Args:
        target_url: Target URL to find proxy for (e.g., "https://api.anthropic.com")
        settings_manager: Optional SettingsManager to check for http_proxy settings

    Returns:
        Proxy URL (e.g., "http://proxy.example.com:8080") or None if no proxy

    Raises:
        ValueError: If proxy URL uses unsupported protocol (SOCKS, PAC)
    """
    parsed = _parse_proxy_target_url(target_url)
    if not parsed:
        return None

    protocol, hostname, port = parsed

    # Check settings first (takes precedence over env vars)
    if settings_manager:
        settings_proxy = settings_manager.get_proxy_url()
        if settings_proxy:
            # Check NO_PROXY exclusions from settings
            no_proxy = settings_manager.get_no_proxy()
            if not _should_proxy_hostname(hostname, port, no_proxy):
                return None
            _validate_proxy_url(settings_proxy)
            return settings_proxy

    # Fall back to environment variables
    if not _should_proxy_hostname(hostname, port):
        return None

    proxy = _get_proxy_env(f"{protocol}_proxy") or _get_proxy_env("all_proxy")
    if not proxy:
        return None

    if "://" not in proxy:
        # Add scheme if missing
        proxy = f"{protocol}://{proxy}"

    _validate_proxy_url(proxy)
    return proxy


def _validate_proxy_url(proxy: str) -> None:
    """Validate proxy URL format and protocol."""
    try:
        parsed = urlparse(proxy)
    except Exception as e:
        raise ValueError(f"Invalid proxy URL {proxy!r}: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{UNSUPPORTED_PROXY_PROTOCOL_MESSAGE} Got {parsed.scheme!r}")


def get_proxies_for_client(
    api_base_url: str, settings_manager: SettingsManager | None = None
) -> dict[str, str] | None:
    """
    Get proxy configuration dict for httpx.AsyncClient or requests.

    Returns a dict keyed by URL scheme; build httpx transports/mounts from it:
        proxies = get_proxies_for_client(api_base_url)
        mounts = {s: httpx.AsyncHTTPTransport(proxy=u) for s, u in proxies.items()} if proxies else None
        async with httpx.AsyncClient(mounts=mounts) as client:
            ...

    Args:
        api_base_url: Base URL of the API (e.g., "https://api.anthropic.com")
        settings_manager: Optional SettingsManager to check for http_proxy settings

    Returns:
        {"http://": proxy_url, "https://": proxy_url} or None if no proxy needed

    Raises:
        ValueError: If proxy URL uses unsupported protocol
    """
    proxy_url = get_proxy_url_for_target(api_base_url, settings_manager)
    if not proxy_url:
        return None

    # httpx expects separate keys for http and https
    return {
        "http://": proxy_url,
        "https://": proxy_url,
    }


def get_proxy_headers(
    settings_manager: SettingsManager | None = None,
) -> dict[str, str] | None:
    """
    Get custom proxy headers for proxy authentication.

    Some corporate proxies require custom headers for authentication (e.g., "Proxy-Authorization").

    Args:
        settings_manager: Optional SettingsManager to check for custom proxy headers

    Returns:
        Dict of custom headers to send to proxy, or None if none configured

    Example:
        headers = get_proxy_headers(settings_manager)
        proxy_headers = {**(headers or {}), "X-Custom-Auth": "token"}
    """
    if not settings_manager:
        return None
    return settings_manager.get_proxy_headers()
