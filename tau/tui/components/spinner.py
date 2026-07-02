from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.theme import SpinnerTheme

if TYPE_CHECKING:
    from tau.tui.tui import TUI


class Spinner(Component):
    """
    Animated spinner with an optional label.

    Appearance is fully controlled by SpinnerTheme — pass a custom theme to
    change frames, speed, and colours without touching this file.

    Usage::

        spinner = Spinner(tui, label="Thinking…")
        spinner.start()
        await agent.invoke(...)
        spinner.stop()
    """

    def __init__(
        self,
        tui: TUI,
        label: str = "",
        theme: SpinnerTheme | None = None,
    ) -> None:
        self._tui = tui
        self._label = label
        self._theme = theme or SpinnerTheme()
        self._frame = 0
        self._active = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

        # Layered "reasons" stacked on top of the legacy active/label state. Each is
        # (key, label); the most recently pushed reason is shown. Independent drivers
        # (e.g. compaction during a turn) push/pop their own key so they can't clobber
        # each other or stop a spinner another driver still needs.
        self._reasons: list[tuple[str, str]] = []

        # Extension overrides — None means "use theme default"
        self._force_hidden: bool = False
        self._custom_frames: list[str] | None = None
        self._custom_interval_ms: int | None = None

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active or bool(self._reasons)

    @property
    def theme(self) -> SpinnerTheme:
        """Return the active spinner theme."""
        return self._theme

    def set_label(self, label: str) -> None:
        self._label = label

    def push_reason(self, key: str, label: str) -> None:
        """Show ``label`` for a named reason, layered above the base state.

        Re-pushing an existing key updates its label and moves it to the top.
        The spinner stays visible until every pushed reason is popped, so an
        independent driver can't be switched off by another's ``stop()``.
        """
        self._reasons = [(k, lbl) for (k, lbl) in self._reasons if k != key]
        self._reasons.append((key, label))
        self._sync_task()
        self._tui.request_render()

    def pop_reason(self, key: str) -> None:
        """Remove a reason previously shown via :meth:`push_reason`.

        When the last reason is removed the spinner falls back to the base
        active/label state (e.g. an in-progress turn's "Thinking…").
        """
        self._reasons = [(k, lbl) for (k, lbl) in self._reasons if k != key]
        self._sync_task()
        self._tui.request_render()

    def set_theme(self, theme: SpinnerTheme) -> None:
        self._theme = theme

    def set_force_hidden(self, hidden: bool) -> None:
        self._force_hidden = hidden
        self._tui.request_render()

    def set_custom_indicator(
        self,
        frames: list[str] | None = None,
        interval_ms: int | None = None,
    ) -> None:
        self._custom_frames = frames
        self._custom_interval_ms = interval_ms

    def start(self) -> None:
        self._active = True
        self._sync_task()

    def stop(self) -> None:
        """Clear the base active state. Layered reasons (push_reason) are unaffected."""
        self._active = False
        self._sync_task()
        self._tui.request_render()

    def dispose(self) -> None:
        """Stop animation and release transient reason state."""
        self._active = False
        self._reasons.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _sync_task(self) -> None:
        """Start or stop the animation task to match the combined active state."""
        if self.active:
            if self._task is None:
                self._frame = 0
                self._task = asyncio.ensure_future(self._run())
        elif self._task is not None:
            self._task.cancel()
            self._task = None
            self._frame = 0

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        if not self.active or self._force_hidden:
            return []
        t = self._theme
        frames = self._custom_frames if self._custom_frames is not None else (t.frames or ["…"])
        char = frames[self._frame % len(frames)]
        frame = t.frame_color(char)
        # A layered reason (e.g. "Compacting…") takes precedence over the base label.
        text = self._reasons[-1][1] if self._reasons else self._label
        label = f" {t.label_color(text)}" if text else ""
        return [(frame + label)[:width]]

    # -------------------------------------------------------------------------
    # Animation loop
    # -------------------------------------------------------------------------

    async def _run(self) -> None:
        interval_ms = (
            self._custom_interval_ms
            if self._custom_interval_ms is not None
            else self._theme.interval_ms
        )
        interval = max(0.05, interval_ms / 1000)
        frames = (
            self._custom_frames
            if self._custom_frames is not None
            else (self._theme.frames or ["…"])
        )
        try:
            while self.active:
                await asyncio.sleep(interval)
                self._frame = (self._frame + 1) % max(1, len(frames))
                # Skip the render request if one is already pending — during
                # streaming the token handler already schedules 60fps renders,
                # so the spinner doesn't need to add redundant wakeups.
                if not self._tui._render_requested:
                    self._tui.request_render()
        except asyncio.CancelledError:
            pass
