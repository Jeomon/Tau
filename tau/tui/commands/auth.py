"""Auth command handlers (login/logout)."""

# type: ignore
from __future__ import annotations

import asyncio
import logging

from tau.tui.commands.context import CommandContext

_log = logging.getLogger(__name__)


def open_login_selector(ctx: CommandContext) -> None:
    """Step 1 — choose auth type: subscription (OAuth) or API key."""
    from tau.inference.api.text.service import TextLLM
    from tau.inference.provider.types import APIProvider, OAuthProvider
    from tau.tui.components.select_list import SelectItem

    has_oauth = any(isinstance(p, OAuthProvider) for p in TextLLM._providers.list())  # type: ignore[union-attr]
    has_api = any(isinstance(p, APIProvider) for p in TextLLM._providers.list())  # type: ignore[union-attr]

    if has_oauth and has_api:
        items = [
            SelectItem(
                label="Subscription",
                description="OAuth — GitHub Copilot, OpenAI Codex, etc.",
                value="oauth",
            ),
            SelectItem(
                label="API key",
                description="A static key, $ENV_VAR, or !command for a provider",
                value="api_key",
            ),
        ]

        def on_type(auth_type: str) -> None:
            if auth_type == "oauth":
                open_oauth_provider_selector(ctx)
            else:
                open_api_key_provider_selector(ctx)

        ctx.layout.open_tree_selector(items, on_type, lambda: ctx.notify("Login cancelled."))
    elif has_oauth:
        open_oauth_provider_selector(ctx)
    else:
        open_api_key_provider_selector(ctx)


def _provider_items(providers: list) -> list:
    from tau.tui.components.select_list import SelectItem

    return [SelectItem(label=p.name, description=p.id, value=p.id) for p in providers]


def open_oauth_provider_selector(ctx: CommandContext) -> None:
    """Step 2 (OAuth path) — pick which OAuth provider to log in to."""
    from tau.inference.api.text.service import TextLLM
    from tau.inference.provider.types import OAuthProvider

    providers = [p for p in TextLLM._providers.list() if isinstance(p, OAuthProvider)]  # type: ignore[union-attr]
    if not providers:
        ctx.notify("No subscription providers available.")
        return

    items = _provider_items(providers)

    def on_pick(provider_id: str) -> None:
        asyncio.ensure_future(run_oauth_login(ctx, provider_id))

    ctx.layout.open_tree_selector(items, on_pick, lambda: ctx.notify("Login cancelled."))


async def run_oauth_login(ctx: CommandContext, provider_id: str) -> None:
    """Run the full OAuth login flow, wiring callbacks to the TUI."""
    import webbrowser

    from tau.inference.api.text.service import TextLLM
    from tau.inference.provider.oauth.types import OAuthAuthInfo, OAuthLoginCallbacks, OAuthPrompt
    from tau.inference.provider.types import OAuthProvider
    from tau.tui.ansi import BOLD, DIM, RESET

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
    """Step 2 (API key path) — pick which provider to save a key for."""
    from tau.inference.api.text.service import TextLLM
    from tau.inference.provider.types import APIProvider

    providers = [p for p in TextLLM._providers.list() if isinstance(p, APIProvider)]  # type: ignore[union-attr]
    if not providers:
        ctx.notify("No API key providers available.")
        return

    items = _provider_items(providers)

    def on_pick(provider_id: str) -> None:
        provider = next((p for p in providers if p.id == provider_id), None)
        if provider is None:
            return
        ctx.layout.open_prompt(
            label=f"API key for {provider.name}  (literal, $ENV_VAR, or !command):",
            on_commit=lambda key: _save_api_key(ctx, provider_id, provider.name, key),
            on_cancel=lambda: ctx.notify("Login cancelled."),
            secret=True,
        )

    ctx.layout.open_tree_selector(items, on_pick, lambda: ctx.notify("Login cancelled."))


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
    from tau.tui.components.select_list import SelectItem

    TextLLM._auth_manager.reload()  # type: ignore[union-attr]
    stored = TextLLM._auth_manager.list()  # type: ignore[union-attr]
    if not stored:
        ctx.notify(
            "No stored credentials. /logout only removes keys saved by /login — "
            "environment variables are unchanged."
        )
        return

    providers = TextLLM._providers.list()  # type: ignore[union-attr]
    provider_map = {p.id: p for p in providers}

    items = [
        SelectItem(
            label=provider_map[pid].name if pid in provider_map else pid,
            description=pid,
            value=pid,
        )
        for pid in stored
    ]

    def on_pick(provider_id: str) -> None:
        name = provider_map[provider_id].name if provider_id in provider_map else provider_id
        TextLLM._auth_manager.remove(provider_id)  # type: ignore[union-attr]
        ctx.notify(f"Removed stored credentials for {name}.")
        if ctx.on_palette_refresh is not None:
            ctx.on_palette_refresh()

    ctx.layout.open_tree_selector(items, on_pick, lambda: ctx.notify("Logout cancelled."))
