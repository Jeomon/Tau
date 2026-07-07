from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.modes.interactive.components.layout import Layout
    from tau.runtime.service import Runtime
    from tau.tui.service import TUI


@dataclass
class CommandContext:
    """Minimal context injected into every TUI command handler.

    This is the single seam between App (orchestrator) and the command
    modules (feature logic). Commands receive this instead of holding a
    reference to the full App, so they can be called from keyboard
    shortcuts, extension APIs, or tests without instantiating App.
    """

    runtime: Runtime
    layout: Layout
    tui: TUI
    on_palette_refresh: Callable[[], None] | None = field(default=None)

    def notify(self, text: str) -> None:
        """Post a system status note to the chat stream."""
        from tau.message.types import CustomMessage, LinesContent

        lines = text.splitlines() + [""]
        contents: list[Any] = [LinesContent(lines=lines)]  # type: ignore[assignment]
        msg = CustomMessage(
            custom_type="system",
            timestamp=time.time(),
            contents=contents,  # type: ignore[arg-type]
        )
        self.layout.add_message(msg)
        self.tui.request_render()
