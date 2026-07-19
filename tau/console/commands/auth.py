from __future__ import annotations

import json

import click


@click.group("auth", context_settings={"help_option_names": ["-h", "--help"]})
def auth():
    """Manage API key credentials."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@auth.command("list")
def auth_list():
    """List all stored credentials with masked keys."""
    data = _load()
    if not data:
        click.echo("No credentials stored.")
        return
    for provider, cred in data.items():
        cred_type = cred.get("type", "?")
        if cred_type == "api_key":
            key = cred.get("key", "")
            masked = key[:6] + "…" + key[-4:] if len(key) > 10 else "***"
            click.echo(f"  {provider:<24} api_key   {masked}")
        else:
            click.echo(f"  {provider:<24} {cred_type}")


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


@auth.command("set")
@click.argument("provider")
@click.argument("key")
def auth_set(provider, key):
    """Store an API key for a PROVIDER."""
    data = _load()
    data[provider] = {"type": "api_key", "key": key}
    _save(data)
    click.echo(f"Saved API key for '{provider}'.")


# ---------------------------------------------------------------------------
# unset
# ---------------------------------------------------------------------------


@auth.command("unset")
@click.argument("provider")
def auth_unset(provider):
    """Remove stored credentials for a PROVIDER."""
    data = _load()
    if provider not in data:
        raise click.ClickException(f"No credentials found for '{provider}'.")
    del data[provider]
    _save(data)
    click.echo(f"Unset credentials for '{provider}'.")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@auth.command("status")
def auth_status():
    """Show configuration status for all known providers."""
    from tau.auth.manager import AuthManager
    from tau.inference.provider.registry import ProviderRegistry
    from tau.inference.provider.types import OAuthProvider

    registry = ProviderRegistry()

    manager = AuthManager.create(registry)

    all_providers = registry.text.list()
    header = f"  {'Provider':<24} {'Type':<8} {'Source':<8} Status"
    separator = "  " + "─" * 54
    click.echo(header)
    click.echo(separator)

    for provider in all_providers:
        status = manager.get_auth_status(provider.id)
        ptype = "oauth" if isinstance(provider, OAuthProvider) else "api_key"

        if status.configured:
            source = click.style(f"{status.source:<8}", fg="cyan")
            indicator = click.style("✓ configured", fg="green")
        else:
            source = click.style(f"{'—':<8}", fg="bright_black")
            indicator = click.style("✗ not configured", fg="bright_black")

        click.echo(f"  {provider.id:<24} {ptype:<8} {source} {indicator}")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@auth.command("login")
@click.argument("provider")
def auth_login(provider):
    """Start an OAuth login flow for a PROVIDER."""
    import asyncio

    asyncio.run(_login(provider))


async def _login(provider_id: str) -> None:
    from tau.auth.manager import AuthManager
    from tau.inference.provider.oauth.types import OAuthAuthInfo, OAuthLoginCallbacks, OAuthPrompt
    from tau.inference.provider.registry import ProviderRegistry

    registry = ProviderRegistry()

    oauth_ids = [p.id for p in registry.text.get_oauth_providers()]
    if provider_id not in oauth_ids:
        raise click.ClickException(
            f"'{provider_id}' does not support OAuth. "
            f"Use 'tau auth set {provider_id} <key>' instead."
        )

    manager = AuthManager.create(registry)

    async def on_prompt(prompt: OAuthPrompt) -> str:
        """Prompt user for input during OAuth flow."""
        return click.prompt(prompt.message, default="")

    def on_auth(auth_info: OAuthAuthInfo) -> None:
        """Display authorization info to the user."""
        msg = f"Open this URL to authenticate:\n\n  {auth_info.url}\n"
        if auth_info.instructions:
            msg += f"{auth_info.instructions}\n"
        click.echo(msg)

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
    )

    click.echo(f"Logging in to '{provider_id}'…")
    await manager.login(provider_id, callbacks)
    click.echo(click.style(f"✓ Logged in to '{provider_id}'.", fg="green"))


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


@auth.command("logout")
@click.argument("provider")
def auth_logout(provider):
    """Revoke OAuth credentials for a PROVIDER."""
    import asyncio

    asyncio.run(_logout(provider))


async def _logout(provider_id: str) -> None:
    from tau.auth.manager import AuthManager
    from tau.inference.provider.registry import ProviderRegistry

    registry = ProviderRegistry()

    manager = AuthManager.create(registry)

    if not manager.has(provider_id):
        raise click.ClickException(f"No stored credentials found for '{provider_id}'.")

    await manager.logout(provider_id)
    click.echo(click.style(f"✓ Logged out of '{provider_id}'.", fg="green"))


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _load() -> dict:
    from tau.settings.paths import get_auth_path

    path = get_auth_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    from tau.settings.paths import get_auth_path

    path = get_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
