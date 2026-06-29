"""Built-in header extension — shows app name and version above the message list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI


def register(tau: ExtensionAPI) -> None:
    from tau.settings.paths import get_app_name, get_app_version
    from tau.tui.component import StaticComponent
    from tau.tui.utils import BOLD, CYAN, DIM, RESET

    def _build() -> StaticComponent:
        name = BOLD + CYAN + get_app_name() + RESET
        version = DIM + f"v{get_app_version()}" + RESET
        return StaticComponent([f"{name} {version}"])

    @tau.on("tui_ready")
    def on_ready(event: Any, ctx: Any) -> None:
        if ctx.has_ui:
            ctx.ui.set_header(_build())
