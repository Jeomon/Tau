from __future__ import annotations

from pathlib import Path
from typing import Literal

from tau.modes.interactive.commands.context import CommandContext


def open_config_panel(ctx: CommandContext) -> None:
    """Open the extension config selector (enable/disable per scope)."""
    from tau.modes.interactive.components.config_selector import ConfigEntry

    sm = ctx.runtime.settings_manager
    if sm is None:
        ctx.notify("Settings unavailable.")
        return

    def _display(path: str) -> str:
        try:
            p = Path(path).expanduser()
            home = Path.home()
            return "~/" + str(p.relative_to(home)) if p.is_relative_to(home) else str(p)
        except Exception:
            return path

    def _manifest_meta(path: str) -> tuple[str | None, str | None]:
        """Best-effort read of an extension's manifest for (title, author)."""
        import json

        from tau.settings.paths import get_app_name

        try:
            p = Path(path).expanduser()
            manifest = (p if p.is_dir() else p.parent) / "manifest.json"
            if not manifest.is_file():
                return None, None
            app = json.loads(manifest.read_text(encoding="utf-8")).get(
                get_app_name().lower(), {}
            )
            title = app.get("name") or (app.get("settings") or {}).get("title")
            return title, app.get("author")
        except Exception:
            return None, None

    def _entry(e, scope: Literal["global", "project"]) -> ConfigEntry:
        title, m_author = _manifest_meta(e.path)
        name = e.name or title or Path(e.path).name
        return ConfigEntry(
            path=e.path,
            name=name,
            author=e.author or m_author,
            path_display=_display(e.path),
            enabled=e.enabled,
            scope=scope,
        )

    global_list = (
        list(sm.global_settings.extensions.list)
        if sm.global_settings.extensions and sm.global_settings.extensions.list
        else []
    )
    project_list = (
        list(sm.project_settings.extensions.list)
        if sm.project_settings.extensions and sm.project_settings.extensions.list
        else []
    )

    all_entries = [_entry(e, "global") for e in global_list] + [
        _entry(e, "project") for e in project_list
    ]

    if not all_entries:
        ctx.notify("No extensions configured. Add extension paths to settings first.")
        return

    changed = False

    def on_toggle(entry: ConfigEntry, enabled: bool) -> None:
        nonlocal changed
        if entry.scope == "global":
            for ext in global_list:
                if ext.path == entry.path:
                    ext.enabled = enabled
                    break
            sm.set_extension_list(global_list)
        else:
            for ext in project_list:
                if ext.path == entry.path:
                    ext.enabled = enabled
                    break
            sm.set_project_extension_list(project_list)

        changed = True
        state = "enabled" if enabled else "disabled"
        ctx.notify(f"Extension {entry.name} {state} ({entry.scope})")

    def on_close() -> None:
        # Apply toggles live: reloading re-discovers extensions against the new
        # enabled set, which loads/unloads their commands, tools, prompt appends
        # and /settings panels without requiring a restart. Batched here so
        # multiple toggles trigger a single reload.
        if changed:
            import asyncio

            asyncio.ensure_future(ctx.runtime.reload_extensions())

    ctx.layout.open_config_selector(all_entries, on_toggle, on_close)
