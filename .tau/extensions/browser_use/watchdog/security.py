"""URL security policy and browser enforcement."""

from __future__ import annotations

import ipaddress
from typing import ClassVar
from urllib.parse import urlsplit

from ..browser.hooks import (
    BrowserErrorEvent,
    BrowserEvent,
    NavigationCompleteEvent,
    TabCreatedEvent,
)
from ..browser.types import NavigationBlockedError

from .base import BaseWatchdog


_BLANK_TAB_URLS = frozenset(
    {"chrome://newtab/", "chrome://new-tab-page/", "chrome://new-tab-page"}
)


class SecurityWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        TabCreatedEvent,
        NavigationCompleteEvent,
    )
    EMITS: ClassVar[tuple[type[BrowserEvent], ...]] = (BrowserErrorEvent,)

    def is_url_allowed(self, url: str) -> tuple[bool, str | None]:
        # Chrome's own new-tab bootstrap page — every manually opened tab
        # (Cmd+T, "+", "open link in new tab") starts here before the user
        # (or a navigation) picks a real destination. It isn't in
        # allowed_url_schemes (only http/https/about/data), so without this
        # exemption on_TabCreatedEvent below would close_target() it on
        # sight — killing every manually opened tab within milliseconds,
        # even though the tool's own new_page("about:blank") path is fine.
        # Treated like about:blank: a neutral starting state, not a real
        # navigation to police.
        if url in _BLANK_TAB_URLS:
            return True, None
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False, "invalid_url"

        scheme = parsed.scheme.lower()
        if scheme not in self.browser.settings.allowed_url_schemes:
            return False, "blocked_scheme"
        if scheme == "about":
            return (url == "about:blank", None if url == "about:blank" else "blocked_about_url")
        if scheme == "data":
            return True, None

        host = _normalize_host(parsed.hostname)
        if not host:
            return False, "missing_hostname"
        if (
            not self.browser.settings.allow_local_network
            and _is_local_or_private(host)
        ):
            return False, "local_network_blocked"

        if any(
            _matches_domain(host, pattern)
            for pattern in self.browser.settings.blocked_domains
        ):
            return False, "blocked_domain"

        allowed = self.browser.settings.allowed_domains
        if allowed and not any(_matches_domain(host, pattern) for pattern in allowed):
            return False, "domain_not_allowed"
        return True, None

    async def ensure_url_allowed(
        self,
        url: str,
        *,
        target_id: str = "",
    ) -> None:
        allowed, reason = self.is_url_allowed(url)
        if allowed:
            return
        await self.browser.hooks.emit(
            BrowserErrorEvent(
                error_type="NavigationBlocked",
                message=f"navigation blocked for {url}: {reason}",
                details={
                    "url": url,
                    "target_id": target_id,
                    "reason": reason,
                },
            )
        )
        raise NavigationBlockedError(f"navigation to {url!r} blocked: {reason}")

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        allowed, reason = self.is_url_allowed(event.url)
        if allowed or self.browser.client is None:
            return
        await self.browser.hooks.emit(
            BrowserErrorEvent(
                error_type="TabCreationBlocked",
                message=f"tab blocked for {event.url}: {reason}",
                details={
                    "url": event.url,
                    "target_id": event.target_id,
                    "reason": reason,
                },
            )
        )
        await self.browser.client.target.close_target(
            {"targetId": event.target_id}
        )

    async def on_NavigationCompleteEvent(
        self, event: NavigationCompleteEvent
    ) -> None:
        allowed, reason = self.is_url_allowed(event.url)
        if allowed or self.browser.client is None or not event.target_id:
            return
        session_id = self.browser.session.target_to_session.get(event.target_id)
        if session_id:
            await self.browser.client.page.navigate(
                {"url": "about:blank"},
                session_id=session_id,
            )
        await self.browser.hooks.emit(
            BrowserErrorEvent(
                error_type="RedirectBlocked",
                message=f"redirect blocked for {event.url}: {reason}",
                details={
                    "url": event.url,
                    "target_id": event.target_id,
                    "reason": reason,
                },
            )
        )


def _normalize_host(host: str | None) -> str:
    if not host:
        return ""
    try:
        return host.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _matches_domain(host: str, pattern: str) -> bool:
    normalized = _normalize_host(pattern)
    if normalized.startswith("*."):
        root = normalized[2:]
        return host == root or host.endswith(f".{root}")
    return host == normalized


def _is_local_or_private(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    address = _parse_ip_address(host.strip("[]"))
    if address is None:
        return False
    return not address.is_global


def _parse_ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP address, including obfuscated IPv4 encodings used to bypass allowlists.

    Attackers can encode an IPv4 address as a single decimal/octal/hex integer
    (e.g. ``http://2130706433/`` == ``127.0.0.1``) or mix numeric bases between
    octets (e.g. ``0x7f.0.0.1``). ``ipaddress.ip_address`` only accepts the
    canonical dotted-quad form, so those forms would otherwise slip past the
    local-network check.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    return _parse_obfuscated_ipv4(host)


def _parse_obfuscated_ipv4(host: str) -> ipaddress.IPv4Address | None:
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values: list[int] = []
    for part in parts:
        if not part:
            return None
        try:
            if part.lower().startswith("0x"):
                value = int(part, 16)
            elif len(part) > 1 and part.startswith("0") and part.isdigit():
                value = int(part, 8)
            elif part.isdigit():
                value = int(part, 10)
            else:
                return None
        except ValueError:
            return None
        values.append(value)

    if any(value < 0 for value in values):
        return None

    # Standard inet_aton-style shorthand: every part except the last is one
    # octet; the last part absorbs however many bits remain (32 - 8*(n-1)).
    if any(value > 0xFF for value in values[:-1]):
        return None
    remaining_bits = 32 - 8 * (len(values) - 1)
    if values[-1] > (1 << remaining_bits) - 1:
        return None

    packed_int = 0
    for value in values[:-1]:
        packed_int = (packed_int << 8) | value
    packed_int = (packed_int << remaining_bits) | values[-1]

    try:
        return ipaddress.IPv4Address(packed_int)
    except (ValueError, OverflowError):
        return None

