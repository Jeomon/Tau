"""Auth command handlers (login/logout)."""

# type: ignore
from __future__ import annotations

import asyncio
import logging

from tau.modes.interactive.commands.context import CommandContext

_log = logging.getLogger(__name__)


def open_login_selector(ctx: CommandContext) -> None:
    """Step 1 — choose auth type: subscription (OAuth) or API key."""
    from tau.modes.interactive.components.oauth_selector import OAuthProviderItem

    provs = _all_providers()
    has_oauth = any(is_oauth for (_id, _name, is_oauth, _key) in provs)
    has_api = any((not is_oauth) and needs_key for (_id, _name, is_oauth, needs_key) in provs)

    if has_oauth and has_api:
        items = [
            OAuthProviderItem(
                id="oauth", name="Subscription", status="OAuth — GitHub Copilot, OpenAI Codex, etc."
            ),
            OAuthProviderItem(
                id="api_key", name="API key", status="A static key, $ENV_VAR, or !command"
            ),
        ]

        def on_type(auth_type: str) -> None:
            if auth_type == "oauth":
                open_oauth_provider_selector(ctx)
            else:
                open_api_key_provider_selector(ctx)

        ctx.layout.open_oauth_selector(
            "login", items, on_type, lambda: ctx.notify("Login cancelled.")
        )
    elif has_oauth:
        open_oauth_provider_selector(ctx)
    else:
        open_api_key_provider_selector(ctx)


def _provider_items(providers: list, stored_ids: set | None = None) -> list:
    from tau.modes.interactive.components.oauth_selector import OAuthProviderItem

    stored = stored_ids or set()
    items = []
    for p in providers:
        status = "✓ configured" if p.id in stored else None
        items.append(OAuthProviderItem(id=p.id, name=p.name, status=status))
    return items


def _all_providers() -> list:
    """Union of providers across every modality, deduped by id (text first).

    Every provider carries an ``id`` (registry key / credential id) and a display
    ``name``. Credentials are stored in one shared file keyed by id, so a key
    saved here works for whichever modalities use that provider. Returns a list
    of ``(id, name, is_oauth, needs_key)`` tuples.
    """
    from tau.inference.api.audio.service import AudioLLM
    from tau.inference.api.image.service import ImageLLM
    from tau.inference.api.text.service import TextLLM
    from tau.inference.api.video.service import VideoLLM
    from tau.inference.provider.types import OAuthProvider
    from tau.inference.types import AuthType

    AudioLLM._ensure_defaults()
    registries = [
        TextLLM._builtin_providers(),
        AudioLLM._providers,
        ImageLLM._providers,
        VideoLLM._providers,
    ]
    seen: set[str] = set()
    out: list = []
    for reg in registries:
        if reg is None:
            continue
        for p in reg.list():
            if p.id in seen:
                continue
            seen.add(p.id)
            needs_key = getattr(p, "auth_type", None) != AuthType.None_
            out.append((p.id, p.name, isinstance(p, OAuthProvider), needs_key))
    return out


def open_oauth_provider_selector(ctx: CommandContext) -> None:
    """Step 2 (OAuth path) — pick which OAuth provider to log in to."""
    from tau.inference.api.text.service import TextLLM
    from tau.inference.provider.types import OAuthProvider

    providers = [p for p in TextLLM._providers.list() if isinstance(p, OAuthProvider)]  # type: ignore[union-attr]
    if not providers:
        ctx.notify("No subscription providers available.")
        return

    stored = set(TextLLM._auth_manager.list())  # type: ignore[union-attr]
    items = _provider_items(providers, stored)

    def on_pick(provider_id: str) -> None:
        asyncio.ensure_future(run_oauth_login(ctx, provider_id))

    ctx.layout.open_oauth_selector("login", items, on_pick, lambda: ctx.notify("Login cancelled."))


async def run_oauth_login(ctx: CommandContext, provider_id: str) -> None:
    """Run the full OAuth login flow, wiring callbacks to the TUI."""
    import webbrowser

    from tau.inference.api.text.service import TextLLM
    from tau.inference.provider.oauth.types import OAuthAuthInfo, OAuthLoginCallbacks, OAuthPrompt
    from tau.inference.provider.types import OAuthProvider
    from tau.tui.utils import BOLD, DIM, RESET

    provider = next(
        (
            p
            for p in TextLLM._providers.list()  # type: ignore[union-attr]
            if isinstance(p, OAuthProvider) and p.id == provider_id
        ),
        None,
    )
    if provider is None:
        ctx.notify(f"Provider '{provider_id}' not found.")
        return

    ctx.layout.open_oauth_status(
        [
            f"  {BOLD}Logging in to {provider.name}{RESET}"
            f"  {DIM}(Esc not available during flow){RESET}",
        ]
    )

    _prompt_future: asyncio.Future[str] | None = None

    def on_auth(info: OAuthAuthInfo) -> None:
        ctx.layout.open_oauth_status(
            [
                f"  {BOLD}Logging in to {provider.name}{RESET}",
                "",
                f"  {DIM}Open this URL in your browser:{RESET}",
                f"  {info.url}",
            ]
        )
        if info.instructions:
            ctx.layout.update_oauth_status(f"  {DIM}{info.instructions}{RESET}")
        webbrowser.open(info.url)

    async def on_prompt(prompt: OAuthPrompt) -> str:
        nonlocal _prompt_future
        loop = asyncio.get_event_loop()
        _prompt_future = loop.create_future()
        label = prompt.message
        if prompt.placeholder:
            label += f"  ({DIM}{prompt.placeholder}{RESET})"
        ctx.layout.close_oauth_status()
        ctx.layout.open_prompt(
            label=label,
            on_commit=lambda val: (
                _prompt_future.set_result(val) if not _prompt_future.done() else None  # type: ignore[union-attr]
            ),
            on_cancel=lambda: (
                _prompt_future.set_exception(asyncio.CancelledError())  # type: ignore[union-attr]
                if not _prompt_future.done()  # type: ignore[union-attr]
                else None
            ),
            secret=False,
        )
        try:
            return await _prompt_future
        except asyncio.CancelledError:
            raise ValueError("Login cancelled") from None

    def on_progress(message: str) -> None:
        ctx.layout.open_oauth_status(
            [
                f"  {BOLD}Logging in to {provider.name}{RESET}",
                f"  {DIM}{message}{RESET}",
            ]
        )

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_progress=on_progress,
    )

    try:
        await TextLLM._auth_manager.login(provider_id, callbacks)  # type: ignore[union-attr]
        ctx.layout.close_oauth_status()
        ctx.notify(f"Logged in to {provider.name}. Credentials saved.")
        if ctx.on_palette_refresh is not None:
            ctx.on_palette_refresh()
    except asyncio.CancelledError:
        ctx.layout.close_oauth_status()
        ctx.notify("Login cancelled.")
    except Exception as exc:
        ctx.layout.close_oauth_status()
        msg = str(exc)
        if msg and msg.lower() != "login cancelled":
            ctx.notify(f"Login failed: {msg}")


def open_api_key_provider_selector(ctx: CommandContext) -> None:
    """Step 2 (API key path) — pick which provider to save a key for.

    Lists API-key providers across all modalities (text/audio/image/video).
    """
    from tau.inference.api.text.service import TextLLM
    from tau.modes.interactive.components.oauth_selector import OAuthProviderItem

    providers = [
        (pid, name)
        for (pid, name, is_oauth, needs_key) in _all_providers()
        if not is_oauth and needs_key
    ]
    if not providers:
        ctx.notify("No API key providers available.")
        return

    stored = set(TextLLM._auth_manager.list())  # type: ignore[union-attr]
    items = [
        OAuthProviderItem(id=pid, name=name, status="✓ configured" if pid in stored else None)
        for pid, name in providers
    ]
    name_by_id = dict(providers)

    def on_pick(provider_id: str) -> None:
        name = name_by_id.get(provider_id, provider_id)
        ctx.layout.open_prompt(
            label=f"API key for {name}  (literal, $ENV_VAR, or !command):",
            on_commit=lambda key: _save_api_key(ctx, provider_id, name, key),
            on_cancel=lambda: ctx.notify("Login cancelled."),
            secret=False,
        )

    ctx.layout.open_oauth_selector("login", items, on_pick, lambda: ctx.notify("Login cancelled."))


def _save_api_key(ctx: CommandContext, provider_id: str, provider_name: str, key: str) -> None:
    from tau.auth.types import APICredential
    from tau.inference.api.text.service import TextLLM

    key = key.strip()
    if not key:
        ctx.notify("API key cannot be empty.")
        return
    TextLLM._auth_manager.set(provider_id, APICredential(key=key))  # type: ignore[union-attr]
    # $ENV_VAR / !command references are stored as-is and resolved at runtime.
    ref = " (resolved at runtime)" if key[:1] in ("$", "!") else ""
    ctx.notify(f"API key saved for {provider_name}.{ref}")
    if ctx.on_palette_refresh is not None:
        ctx.on_palette_refresh()


def get_palette_overrides() -> dict[str, str]:
    """Return dynamic palette description overrides for /login and /logout."""
    overrides: dict[str, str] = {}
    try:
        from tau.inference.api.text.service import TextLLM

        auth = TextLLM._auth_manager  # type: ignore[union-attr]
        auth.reload()  # type: ignore[union-attr]
        logged_in = auth.list()  # type: ignore[union-attr]
        if logged_in:
            providers_str = ", ".join(logged_in)
            overrides["login"] = f"Add credentials  ·  active: {providers_str}"
            overrides["logout"] = f"Remove credentials  ·  active: {providers_str}"
        else:
            overrides["login"] = "Add credentials  ·  none active"
            overrides["logout"] = "Remove credentials  ·  none active"
    except Exception:
        _log.debug("failed to load auth state for login menu", exc_info=True)
    return overrides


def open_logout_selector(ctx: CommandContext) -> None:
    from tau.inference.api.text.service import TextLLM
    from tau.modes.interactive.components.oauth_selector import OAuthProviderItem

    TextLLM._auth_manager.reload()  # type: ignore[union-attr]
    stored = TextLLM._auth_manager.list()  # type: ignore[union-attr]
    if not stored:
        ctx.notify(
            "No stored credentials. /logout only removes keys saved by /login — "
            "environment variables are unchanged."
        )
        return

    name_by_id = {pid: name for (pid, name, _o, _k) in _all_providers()}

    items = [
        OAuthProviderItem(id=pid, name=name_by_id.get(pid, pid), status="✓ configured")
        for pid in stored
    ]

    def on_pick(provider_id: str) -> None:
        name = name_by_id.get(provider_id, provider_id)
        TextLLM._auth_manager.remove(provider_id)  # type: ignore[union-attr]
        ctx.notify(f"Removed stored credentials for {name}.")
        if ctx.on_palette_refresh is not None:
            ctx.on_palette_refresh()

    ctx.layout.open_oauth_selector(
        "logout", items, on_pick, lambda: ctx.notify("Logout cancelled.")
    )
