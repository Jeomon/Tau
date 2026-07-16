from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.auth.manager import AuthManager
from tau.auth.types import APICredential
from tau.builtins.providers.text import api_providers, oauth_providers
from tau.inference.model.registry import ModelRegistry
from tau.inference.provider.oauth.types import OAuthAuthInfo, OAuthLoginCallbacks, OAuthPrompt
from tau.inference.provider.registry import ProviderRegistry
from tau.inference.provider.types import OAuthProvider

if TYPE_CHECKING:
    from tau.inference.model.types import Model
    from tau.runtime.service import Runtime


def _build_auth_manager() -> AuthManager:
    """A fresh AuthManager wired to the builtin text providers, matching `tau auth` CLI's own setup."""
    registry = ProviderRegistry()
    for provider in api_providers + oauth_providers:
        registry.text.register(provider)
    return AuthManager.create(registry)


class SettingsDialog:
    """Model picker + API key manager, opened from the top bar's gear icon."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._dialog: Any | None = None
        self._models_container: Any | None = None
        self._keys_container: Any | None = None
        self._oauth_dialog: Any | None = None
        self._oauth_content: Any | None = None
        self._oauth_busy = False

    def render(self) -> None:
        """Build the (initially hidden) dialog."""
        with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw] tau-settings-card"):
            with ui.tabs().classes("w-full") as tabs:
                models_tab = ui.tab("Model")
                keys_tab = ui.tab("API Keys")
            with ui.tab_panels(tabs, value=models_tab).classes("w-full"):
                with ui.tab_panel(models_tab):
                    self._models_container = ui.column().classes(
                        "w-full gap-1 max-h-[60vh] overflow-auto"
                    )
                with ui.tab_panel(keys_tab):
                    self._keys_container = ui.column().classes(
                        "w-full gap-2 max-h-[60vh] overflow-auto"
                    )
        self._dialog = dialog

        with ui.dialog() as oauth_dialog, ui.card().classes("w-[420px] max-w-[90vw] tau-settings-card"):
            ui.label("OAuth login").classes("text-sm font-semibold text-[var(--text)]")
            self._oauth_content = ui.column().classes("w-full gap-2")
        self._oauth_dialog = oauth_dialog

        self._render_keys()

    def open(self) -> None:
        """Refresh and show the dialog."""
        self._render_models()
        if self._dialog is not None:
            self._dialog.open()

    def _current_model(self) -> tuple[str | None, str | None]:
        agent = self._runtime.agent
        llm = agent._engine.llm if agent is not None else None
        if llm is None:
            return None, None
        return getattr(llm.model, "id", None), getattr(llm, "provider_id", None)

    def _render_models(self) -> None:
        if self._models_container is None:
            return
        self._models_container.clear()

        registry = ModelRegistry.from_text_builtins()
        current_id, current_provider = self._current_model()

        by_provider: dict[str, list[Model]] = {}
        for model in registry.list():
            by_provider.setdefault(model.provider, []).append(model)

        with self._models_container:
            for provider_id in sorted(by_provider):
                with ui.expansion(provider_id).classes("w-full tau-thinking-block"):
                    for model in sorted(by_provider[provider_id], key=lambda m: m.name):
                        active = model.id == current_id and model.provider == current_provider
                        classes = "w-full px-2 py-1 tau-session-row" + (
                            " tau-active" if active else ""
                        )
                        with ui.row().classes(classes) as row:
                            ui.label(model.name).classes("text-xs text-[var(--text)]")
                        row.on("click", lambda m=model: self._select_model(m))

    async def _select_model(self, model: Model) -> None:
        ok = await self._runtime.set_model(model.id, model.provider)
        if ok:
            ui.notify(f"Switched to {model.name}", type="positive")
            self._render_models()
            if self._dialog is not None:
                self._dialog.close()
        else:
            ui.notify(f"Could not switch to {model.name} — check its API key", type="negative")

    def _render_keys(self) -> None:
        if self._keys_container is None:
            return
        self._keys_container.clear()
        manager = _build_auth_manager()

        with self._keys_container:
            for provider in api_providers + oauth_providers:
                status = manager.get_auth_status(provider.id)
                with ui.row().classes("w-full items-center gap-2 px-1"):
                    icon = "check_circle" if status.configured else "cancel"
                    color = "#16a34a" if status.configured else "var(--text-dim)"
                    ui.icon(icon).style(f"color: {color} !important; font-size: 16px;")
                    with ui.column().classes("flex-1 gap-0 min-w-0"):
                        ui.label(provider.name).classes("text-xs text-[var(--text)]")
                        ui.label("configured" if status.configured else "not configured").classes(
                            "text-[10px] " + ("text-[#16a34a]" if status.configured else "text-[var(--text-dim)]")
                        )
                    if isinstance(provider, OAuthProvider):
                        ui.button(
                            "Logout" if status.configured else "Login",
                            on_click=lambda p=provider: self._oauth_login_or_logout(p),
                        ).props("flat dense no-caps size=sm").classes(
                            "text-[var(--text-muted)]" if status.configured else "text-[var(--accent)]"
                        )
                    else:
                        key_input = (
                            ui.input(placeholder="sk-…" if not status.configured else "Enter new key to replace…")
                            .props("dense outlined type=password")
                            .classes("w-40")
                            .style("font-size: 12px;")
                        )
                        key_input.on(
                            "keydown.enter",
                            lambda _e, provider_id=provider.id, inp=key_input: self._save_key(
                                provider_id, inp
                            ),
                        )
                        reveal_btn = ui.button(icon="visibility").props("flat dense round size=sm")
                        reveal_btn.on(
                            "click", lambda _e, inp=key_input: self._toggle_reveal(inp)
                        )
                        reveal_btn.style("color: var(--text-dim) !important;")
                        save_btn = ui.button(icon="save").props("flat dense round size=sm")
                        save_btn.on(
                            "click",
                            lambda _e, provider_id=provider.id, inp=key_input: self._save_key(
                                provider_id, inp
                            ),
                        )
                        save_btn.style("color: var(--text-dim) !important;")
                        if status.configured:
                            remove_btn = ui.button(icon="delete").props("flat dense round size=sm")
                            remove_btn.on(
                                "click",
                                lambda _e, provider_id=provider.id: self._remove_key(provider_id),
                            )
                            remove_btn.style("color: var(--text-dim) !important;")

    def _toggle_reveal(self, key_input: Any) -> None:
        current = key_input.props.get("type", "password")
        key_input.props["type"] = "password" if current == "text" else "text"

    def _save_key(self, provider_id: str, key_input: Any) -> None:
        value = key_input.value
        if not value:
            return
        manager = _build_auth_manager()
        manager.set(provider_id, APICredential(key=value))
        key_input.value = ""
        ui.notify(f"Saved API key for {provider_id}", type="positive")
        self._render_keys()

    def _remove_key(self, provider_id: str) -> None:
        manager = _build_auth_manager()
        manager.remove(provider_id)
        ui.notify(f"Removed API key for {provider_id}", type="positive")
        self._render_keys()

    # -- OAuth ---------------------------------------------------------------

    async def _oauth_login_or_logout(self, provider: OAuthProvider) -> None:
        if self._oauth_busy:
            return
        manager = _build_auth_manager()
        status = manager.get_auth_status(provider.id)
        if status.configured:
            await self._oauth_logout(manager, provider)
        else:
            await self._oauth_login(manager, provider)

    async def _oauth_logout(self, manager: AuthManager, provider: OAuthProvider) -> None:
        try:
            await manager.logout(provider.id)
        except Exception as e:
            ui.notify(f"Logout failed: {e}", type="negative")
            return
        ui.notify(f"Logged out of {provider.name}", type="positive")
        self._render_keys()

    async def _oauth_login(self, manager: AuthManager, provider: OAuthProvider) -> None:
        if self._oauth_content is None or self._oauth_dialog is None:
            return
        self._oauth_busy = True
        self._oauth_content.clear()
        with self._oauth_content:
            ui.label(f"Connecting to {provider.name}…").classes(
                "text-xs text-[var(--text-muted)]"
            )
        self._oauth_dialog.open()

        def on_auth(info: OAuthAuthInfo) -> None:
            if self._oauth_content is None:
                return
            self._oauth_content.clear()
            with self._oauth_content:
                ui.label(f"Open this link to authorize {provider.name}:").classes(
                    "text-xs text-[var(--text-muted)]"
                )
                ui.link(info.url, info.url, new_tab=True).classes(
                    "text-xs break-all text-[var(--accent)]"
                )
                if info.instructions:
                    ui.label(info.instructions).classes("text-xs text-[var(--text-dim)]")
                ui.spinner(size="sm")

        async def on_prompt(prompt: OAuthPrompt) -> str:
            if self._oauth_content is None:
                return ""
            self._oauth_content.clear()
            future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
            with self._oauth_content:
                ui.label(prompt.message).classes("text-xs text-[var(--text-muted)]")
                prompt_input = ui.input(placeholder=prompt.placeholder).props(
                    "dense outlined"
                ).classes("w-full")

                def submit() -> None:
                    value = prompt_input.value or ""
                    if not value and not prompt.allow_empty:
                        return
                    if not future.done():
                        future.set_result(value)

                prompt_input.on("keydown.enter", lambda _e: submit())
                ui.button("Continue", on_click=submit).props("no-caps dense").classes(
                    "self-end"
                )
            return await future

        def on_progress(message: str) -> None:
            if self._oauth_content is None:
                return
            self._oauth_content.clear()
            with self._oauth_content:
                ui.label(message).classes("text-xs text-[var(--text-muted)]")
                ui.spinner(size="sm")

        callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt, on_progress=on_progress)
        try:
            await manager.login(provider.id, callbacks)
        except Exception as e:
            ui.notify(f"Login failed: {e}", type="negative")
        else:
            ui.notify(f"Logged in to {provider.name}", type="positive")
            self._render_keys()
        finally:
            self._oauth_busy = False
            self._oauth_dialog.close()
