"""Model + context-usage badge component."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tau.tui.style import Style
from tau.tui.text import Line, Span

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect


class ModelBadge:
    """Renders ``(provider) model ∙ Level|context%`` for the footer Row right slot.

    The ``∙ Level`` segment only appears when the active model supports
    extended thinking and a thinking level is set.
    """

    def __init__(self) -> None:
        self._provider = ""
        self._model = ""
        self._tokens = 0
        self._context_window = 0
        self._thinking = False
        self._thinking_level = ""

    def set_model(self, model_id: str, provider: str, thinking: bool = False) -> None:
        self._model = model_id
        self._provider = provider
        self._thinking = thinking

    def set_thinking_level(self, level: object) -> None:
        self._thinking_level = str(getattr(level, "value", level) or "")

    def set_context(self, tokens: int, context_window: int) -> None:
        self._tokens = tokens
        self._context_window = context_window

    def update_from_ctx(self, ctx: object) -> None:
        self.set_model(
            getattr(ctx, "model_id", "") or "",
            getattr(ctx, "provider_id", "") or "",
            bool(getattr(ctx, "model_thinking", False)),
        )
        settings = getattr(ctx, "settings", None)
        if settings is not None:
            self.set_thinking_level(settings.get_thinking_level())
        self.update_context_from_ctx(ctx)

    def update_context_from_ctx(self, ctx: object) -> None:
        usage = getattr(ctx, "get_context_usage", lambda: None)()
        if usage is not None:
            tokens = usage.get("tokens") or 0
            window = usage.get("context_window") or 0
            self.set_context(tokens, window)

    def update_context_from_response(self, response: object, ctx: object) -> None:
        """Apply usage from a completed provider response immediately."""
        usage = getattr(response, "usage", None)
        if usage is None:
            self.update_context_from_ctx(ctx)
            return

        cache_read = (
            0
            if getattr(usage, "input_tokens_include_cache_read", False)
            else getattr(usage, "cache_read_tokens", 0) or 0
        )
        tokens = (
            (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "output_tokens", 0) or 0)
            + cache_read
            + (getattr(usage, "cache_write_tokens", 0) or 0)
        )
        context = getattr(ctx, "get_context_usage", lambda: None)()
        window = (context or {}).get("context_window") or self._context_window
        if tokens > 0:
            self.set_context(tokens, window)
        else:
            self.update_context_from_ctx(ctx)

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        from tau.tui.utils import DIM, RESET

        text = self._text()
        return [DIM + text + RESET] if text else []

    def _text(self) -> str:
        if not self._provider and not self._model:
            return ""
        left = f"({self._provider}) {self._model}" if self._provider else self._model
        if self._thinking and self._thinking_level and self._thinking_level != "off":
            left += f" ∙ {self._thinking_level.title()}"
        if self._context_window > 0 and self._tokens > 0:
            pct = self._tokens / self._context_window * 100
            label = f"{pct:.1f}%" if pct < 1 else f"{int(round(pct))}%"
            return f"{left}|{label}"
        return left

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        text = self._text()
        if not text:
            return 0
        buf.grow_to(area.y + 1)
        buf.set_line(area.x, area.y, Line([Span(text, Style().dim())]), area.width)
        return 1

    def handle_input(self, event: object) -> bool:  # noqa: ARG002
        return False

    def invalidate(self) -> None:
        pass
