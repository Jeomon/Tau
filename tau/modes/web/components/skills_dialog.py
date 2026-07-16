from __future__ import annotations

from typing import Any

from nicegui import ui

from tau.skills.registry import skill_registry


class SkillsDialog:
    """Read-only skills browser, opened from the top bar's icon."""

    def __init__(self) -> None:
        self._dialog: Any | None = None
        self._container: Any | None = None

    def render(self) -> None:
        """Build the (initially hidden) dialog."""
        with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw] tau-settings-card"):
            ui.label("Skills").classes("w-full text-sm font-semibold text-[var(--text)] px-1")
            self._container = ui.column().classes("w-full gap-1 max-h-[65vh] overflow-auto")
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
        skills = sorted(skill_registry.list_all(), key=lambda s: s.name)

        with self._container:
            if not skills:
                ui.label("No skills found.").classes("text-xs text-[var(--text-dim)] px-1")
                return
            for skill in skills:
                with ui.expansion(skill.name, caption=skill.description).classes(
                    "w-full tau-thinking-block"
                ):
                    ui.markdown(skill.content).classes("max-w-none text-xs")
