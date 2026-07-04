from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tau.message.types import UserMessage
from tau.modes.interactive.app import App


def test_quiet_startup_still_replays_session_history(tmp_path: Path) -> None:
    message = UserMessage.from_text("resumed message")
    settings = SimpleNamespace(get_quiet_startup=lambda: True)
    session = SimpleNamespace(
        cwd=tmp_path,
        build_session_context=lambda: SimpleNamespace(messages=[message]),
    )
    runtime = SimpleNamespace(
        settings_manager=settings,
        session_manager=session,
        hooks=SimpleNamespace(emit=AsyncMock()),
    )
    layout = SimpleNamespace(
        add_message=Mock(),
        set_commands=Mock(),
        set_cwd=Mock(),
    )
    tui = SimpleNamespace(
        on_input=Mock(),
        run=AsyncMock(),
        on_background_color=None,
    )
    app = object.__new__(App)
    app._runtime = runtime
    app._layout = layout
    app._tui = tui
    app._hooks = SimpleNamespace(subscribe=Mock(), _refresh_model_badge=Mock())
    app._input = SimpleNamespace(load_history=Mock(), bind=Mock())
    app._auto_theme = False
    app._redirect_logging_off_terminal = Mock()
    app._register_ui_commands = Mock()
    app._build_palette_entries = Mock(return_value=[])
    app._register_extension_shortcuts = Mock()
    app._setup_trust_screen_if_needed = Mock()
    app._announce_update = AsyncMock()
    app._track_task = Mock()
    app._cleanup = AsyncMock()

    asyncio.run(app.run())

    layout.add_message.assert_called_once_with(message)
