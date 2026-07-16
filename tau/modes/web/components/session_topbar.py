from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.message.types import Role

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


def _format_tokens(n: int) -> str:
    """Compact token count, e.g. 128000 -> '128k'."""
    if n >= 1_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def _format_cost(total: float) -> str:
    if total <= 0:
        return "$0.00"
    if total < 0.01:
        return f"${total:.4f}"
    return f"${total:.2f}"


def _session_cost(runtime: Runtime) -> float:
    """Sum usage.cost.total across every assistant turn in the current branch."""
    context = runtime.session_manager.build_session_context()
    total = 0.0
    for message in context.messages:
        if getattr(message, "role", None) == Role.ASSISTANT:
            usage = getattr(message, "usage", None)
            if usage is not None:
                total += usage.cost.total
    return total


class SessionTopBar:
    """Slim header above the transcript: model, context usage, and running cost."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        on_toggle_files: Callable[[], None] | None = None,
        on_open_branches: Callable[[], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._on_toggle_files = on_toggle_files
        self._on_open_branches = on_open_branches
        self._model_label: Any | None = None
        self._context_label: Any | None = None
        self._cost_label: Any | None = None

    def render(self) -> None:
        """Render the top bar and subscribe it to session-lifecycle events."""
        with ui.row().classes("w-full items-center gap-4 px-1 pb-2 tau-topbar"):
            self._model_label = ui.label().classes("text-xs font-medium text-[var(--text-muted)]")
            self._context_label = ui.label().classes("text-xs text-[var(--text-dim)]")
            self._cost_label = ui.label().classes("text-xs text-[var(--text-dim)] ml-auto")
            if self._on_open_branches is not None:
                ui.button("Branches", icon="account_tree", on_click=self._on_open_branches).props(
                    "flat no-caps dense"
                ).classes("tau-footer-tab").style("color: var(--text-muted) !important;")
            if self._on_toggle_files is not None:
                ui.button("Files", icon="folder_open", on_click=self._on_toggle_files).props(
                    "flat no-caps dense"
                ).classes("tau-footer-tab").style("color: var(--text-muted) !important;")

        self._refresh()

        async def on_event(event: object) -> None:
            if getattr(event, "type", "") in {"session_start", "message_end", "model_select"}:
                self._refresh()

        unsubs = [
            self._runtime.hooks.register(name, on_event)
            for name in ("session_start", "message_end", "model_select")
        ]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])

    def _refresh(self) -> None:
        if self._model_label is None or self._context_label is None or self._cost_label is None:
            return

        llm = self._runtime.agent._engine.llm if self._runtime.agent is not None else None
        if llm is not None:
            model_name = getattr(llm.model, "name", None) or getattr(llm.model, "id", "unknown")
            self._model_label.text = f"({llm.provider_id}) {model_name}"
        else:
            self._model_label.text = ""

        usage = self._runtime.agent.get_context_usage() if self._runtime.agent is not None else None
        if usage is not None and usage.context_window > 0:
            pct = usage.percent or 0.0
            pct_label = f"{pct:.1f}%" if pct < 1 else f"{round(pct)}%"
            self._context_label.text = (
                f"{_format_tokens(usage.tokens)} / {_format_tokens(usage.context_window)} tokens ({pct_label})"
            )
        else:
            self._context_label.text = ""

        self._cost_label.text = _format_cost(_session_cost(self._runtime))
