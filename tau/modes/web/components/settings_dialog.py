from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.auth.manager import AuthManager
from tau.auth.types import APICredential
from tau.builtins.providers.text import api_providers, oauth_providers
from tau.inference.model.registry import ModelRegistry
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
                    ui.label(provider.name).classes("flex-1 text-xs text-[var(--text)]")
                    if isinstance(provider, OAuthProvider):
                        ui.label("OAuth — use `tau auth login`").classes(
                            "text-xs text-[var(--text-dim)]"
                        )
                    else:
                        key_input = (
                            ui.input(placeholder="API key")
                            .props("dense outlined type=password")
                            .classes("w-40")
                            .style("font-size: 12px;")
                        )
                        save_btn = ui.button(icon="save").props("flat dense round size=sm")
                        save_btn.on(
                            "click",
                            lambda provider_id=provider.id, inp=key_input: self._save_key(
                                provider_id, inp
                            ),
                        )

    def _save_key(self, provider_id: str, key_input: Any) -> None:
        value = key_input.value
        if not value:
            return
        manager = _build_auth_manager()
        manager.set(provider_id, APICredential(key=value))
        key_input.value = ""
        ui.notify(f"Saved API key for {provider_id}", type="positive")
        self._render_keys()
