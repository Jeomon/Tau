"""Closing /settings reports whether anything actually changed.

``on_close`` announced "Settings saved." unconditionally, so opening the panel,
reading a few rows and pressing Escape claimed a write that never happened.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock

from tau.modes.interactive.commands.context import CommandContext
from tau.modes.interactive.commands.settings import open_settings_panel
from tau.settings.manager import SettingsManager
from tau.settings.storage import InMemorySettingsStorage
from tau.settings.types import Settings
from tau.tui.theme import LayoutTheme


def _manager() -> SettingsManager:
    return SettingsManager(
        storage=InMemorySettingsStorage(),
        initial_global=Settings(),
        initial_project=Settings(),
    )


def _panel(manager: SettingsManager) -> tuple[list[str], dict]:
    """Open the panel over ``manager``; hand back its notifications and modal."""
    notifications: list[str] = []
    opened: dict = {}

    layout = SimpleNamespace(
        theme=LayoutTheme(),
        messages=SimpleNamespace(set_show_images=Mock()),
        input=SimpleNamespace(),
        open_settings_selector=lambda modal, on_cancel: opened.update(
            modal=modal, on_cancel=on_cancel
        ),
        add_message=Mock(),
    )
    tui = SimpleNamespace(background_color=None, request_render=Mock())
    runtime = SimpleNamespace(settings_manager=manager, extension_runtime=None)
    ctx = CommandContext(runtime=runtime, layout=layout, tui=tui)  # type: ignore[arg-type]
    ctx.notify = notifications.append  # type: ignore[method-assign]

    open_settings_panel(ctx)
    return notifications, opened


def _visit(manager: SettingsManager, edit: Callable[[], None]) -> list[str]:
    """Open the panel, apply ``edit``, close it, and return what it announced.

    save_batch() schedules its write on the running loop, so the whole visit has
    to happen inside one.
    """
    notifications, opened = _panel(manager)

    async def _run() -> None:
        edit()
        opened["on_cancel"]()
        await manager.flush()

    asyncio.run(_run())
    return notifications


def _noop() -> None:
    pass


def test_closing_without_changes_does_not_claim_a_save() -> None:
    assert _visit(_manager(), _noop) == ["Settings closed — no changes."]


def test_closing_after_a_change_reports_the_save() -> None:
    manager = _manager()

    notices = _visit(manager, lambda: manager.set_quiet_startup(True))

    assert notices == ["Settings saved."]


def test_a_second_change_to_the_same_field_still_reports_a_save() -> None:
    """The manager's modified-field set is sticky, so it cannot be the signal.

    ``quiet_startup`` is already marked modified by the second visit, so a check
    based on that set would call this genuine change "no changes".
    """
    manager = _manager()
    _visit(manager, lambda: manager.set_quiet_startup(True))

    notices = _visit(manager, lambda: manager.set_quiet_startup(False))

    assert notices == ["Settings saved."]


def test_re_selecting_the_value_already_in_force_is_not_a_change() -> None:
    manager = _manager()
    _visit(manager, lambda: manager.set_theme("nord"))

    notices = _visit(manager, lambda: manager.set_theme("nord"))

    assert notices == ["Settings closed — no changes."]


def test_materialising_a_defaulted_field_does_count_as_a_change() -> None:
    """An unset field written for the first time really does change the file."""
    manager = _manager()
    assert manager.settings.quiet_startup is None
    assert manager.get_quiet_startup() is False

    notices = _visit(manager, lambda: manager.set_quiet_startup(False))

    assert notices == ["Settings saved."]


def test_an_extension_config_change_counts_as_a_change() -> None:
    """Extension sub-panels write through set_extension_config_key, not on_change."""
    manager = _manager()

    notices = _visit(
        manager,
        lambda: manager.set_extension_config_key("/ext/thing.py", "enabled", True),
    )

    assert notices == ["Settings saved."]
