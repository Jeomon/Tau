from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.theme import SpinnerTheme
from tau.tui.utils import format_number

if TYPE_CHECKING:
    from tau.tui.service import TUI


def _format_elapsed(seconds: float) -> str:
    """Compact elapsed-time string, e.g. "12s", "1m 3s", "1h 0m 12s".

    Leading zero units (y/d/h/m) are omitted; once the largest nonzero unit
    is reached, every unit below it is shown (including zeros).
    """
    total = max(0, int(seconds))
    years, rem = divmod(total, 365 * 86400)
    days, rem = divmod(rem, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if years:
        parts.append(f"{years}y")
    if days or parts:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


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

        # Per-turn elapsed-time and token stats, shown alongside the label.
        self._turn_started_at: float | None = None
        self._tokens_up = 0
        self._tokens_down = 0
        # Char-based estimate of the still-streaming message's token count,
        # shown live and folded into _tokens_up once real usage lands.
        self._streaming_estimate = 0

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

    def start_turn(self, input_estimate: int = 0) -> None:
        """Start the base active state and reset elapsed time / token stats.

        Call this at the start of a fresh agent turn (instead of ``start()``)
        so the elapsed-time and token counters shown next to the spinner
        reflect this turn rather than accumulating across turns. ``up``
        starts at ``input_estimate`` (a rough tokenizer estimate of the
        user's new message) rather than 0, so it isn't blank until the
        first real usage report lands.
        """
        self._turn_started_at = time.monotonic()
        self._tokens_up = input_estimate
        self._tokens_down = 0
        self._streaming_estimate = 0
        self.start()

    def update_tokens(self, *, up: int | None = None, down: int = 0) -> None:
        """Apply real usage reported by a completed message.

        ``up`` (input tokens) REPLACES the current count rather than adding
        to it: each provider response reports the full prompt size for that
        specific call, so within a turn with several tool-call round trips,
        summing each call's input_tokens would double-count the shared
        prefix every time. ``down`` (output tokens) is genuinely new content
        per call, so it accumulates.
        """
        if up is not None:
            self._tokens_up = up
        self._tokens_down += down
        self._streaming_estimate = 0
        self._tui.request_render()

    def set_streaming_estimate(self, tokens: int) -> None:
        """Update the live token estimate for the message still streaming in.

        ``tokens`` should cover the in-flight message's full content —
        thinking, text, and any tool-call arguments — so the down-count
        climbs during tool-calling too, not just plain text streaming.
        Superseded by :meth:`update_tokens` once real usage arrives.
        """
        if tokens != self._streaming_estimate:
            self._streaming_estimate = tokens
            self._tui.request_render()

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

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        if not self.active or self._force_hidden:
            return 0
        buf.grow_to(area.y + 1)
        t = self._theme
        frames = self._custom_frames if self._custom_frames is not None else (t.frames or ["…"])
        char = frames[self._frame % len(frames)]
        col = buf.set_string(area.x, area.y, char, t.frame_color, max_width=area.width)
        # A layered reason (e.g. "Compacting…") takes precedence over the base label.
        text = self._reasons[-1][1] if self._reasons else self._label
        if text:
            remaining = area.x + area.width - col
            col = buf.set_string(col, area.y, f" {text}", t.label_color, max_width=remaining)
        if self._turn_started_at is not None:
            elapsed = _format_elapsed(time.monotonic() - self._turn_started_at)
            # ↑ = outgoing (prompt/input tokens sent), ↓ = incoming (output
            # tokens received back) — the live estimate tracks the response
            # actually streaming in, so it belongs on the down side.
            up = format_number(self._tokens_up)
            down = format_number(self._tokens_down + self._streaming_estimate)
            stats = f" ({elapsed} · ↑{up} ↓{down})"
            remaining = area.x + area.width - col
            buf.set_string(col, area.y, stats, t.stat_color, max_width=remaining)
        return 1

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
