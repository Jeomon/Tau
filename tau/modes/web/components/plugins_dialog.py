from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

if TYPE_CHECKING:
    from tau.runtime.service import Runtime
    from tau.settings.types import PackageEntry


def _resource_summary(pkg: PackageEntry) -> str:
    if not pkg.enabled:
        return "Disabled"
    parts = [
        f"{len(pkg.extensions)} ext" if pkg.extensions else "",
        f"{len(pkg.skills)} skills" if pkg.skills else "",
        f"{len(pkg.prompts)} prompts" if pkg.prompts else "",
        f"{len(pkg.themes)} themes" if pkg.themes else "",
    ]
    parts = [p for p in parts if p]
    return " · ".join(parts) if parts else "No resources"


class PluginsDialog:
    """Read-only plugin package browser, opened from the top bar."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._dialog: Any | None = None
        self._container: Any | None = None

    def render(self) -> None:
        """Build the (initially hidden) dialog."""
        with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw] tau-settings-card"):
            ui.label("Plugins").classes("w-full text-sm font-semibold text-[var(--text)] px-1")
            self._container = ui.column().classes(
                "w-full min-w-0 items-stretch gap-1 max-h-[65vh] overflow-auto"
            )
        self._dialog = dialog

    def open(self) -> None:
        """Refresh and show the dialog."""
        self._render_list()
        if self._dialog is not None:
            self._dialog.open()

    def _render_list(self) -> None:
        if self._container is None:
            return
        self._container.clear()

        settings = self._runtime.settings_manager
        packages = settings.get_all_packages() if settings is not None else []

        with self._container:
            if not packages:
                ui.label("No plugin packages installed.").classes(
                    "text-xs text-[var(--text-dim)] px-1"
                )
                return
            for pkg in packages:
                with ui.row().classes("w-full items-center gap-2 px-2 py-1 tau-session-row"):
                    icon = "check_circle" if pkg.enabled else "cancel"
                    color = "#16a34a" if pkg.enabled else "var(--text-dim)"
                    ui.icon(icon).style(f"color: {color} !important; font-size: 16px;")
                    with ui.column().classes("flex-1 min-w-0 items-stretch gap-0"):
                        version = f"  {pkg.version}" if pkg.version else ""
                        ui.label(f"{pkg.name}{version}").classes(
                            "w-full min-w-0 truncate text-xs font-medium text-[var(--text)]"
                        )
                        ui.label(_resource_summary(pkg)).classes(
                            "w-full min-w-0 truncate text-[11px] text-[var(--text-dim)]"
                        )
