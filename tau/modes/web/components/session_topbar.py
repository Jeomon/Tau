from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.message.types import Role

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


def _fmt_tokens(n: int) -> str:
    """Compact token count, e.g. 128000 -> '128k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _fmt_cost(total: float) -> str:
    if total <= 0:
        return "$0.00"
    if total < 0.01:
        return "<$0.01"
    return f"${total:.2f}"


@dataclass
class _SessionStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0


def _collect_session_stats(runtime: Runtime) -> _SessionStats:
    """Sum token/cost usage across every assistant turn in the current branch."""
    context = runtime.session_manager.build_session_context()
    stats = _SessionStats()
    for message in context.messages:
        if getattr(message, "role", None) != Role.ASSISTANT:
            continue
        usage = getattr(message, "usage", None)
        if usage is None:
            continue
        stats.input_tokens += usage.input_tokens
        stats.output_tokens += usage.output_tokens
        stats.cache_read_tokens += usage.cache_read_tokens
        stats.cache_write_tokens += usage.cache_write_tokens
        stats.cost += usage.cost.total
    return stats


class SessionTopBar:
    """Slim header above the transcript: model, context usage, and running cost.

    Styled after pi-web's AppShell top bar (tab-style Branches/Files buttons
    on the left, a compact token/cost/context stats cluster on the right)
    rather than the plain text labels this used to be.
    """

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
        self._stats_row: Any | None = None

    def render(self) -> None:
        """Render the top bar and subscribe it to session-lifecycle events."""
        with ui.row().classes("w-full items-stretch gap-0 tau-topbar"):
            self._model_label = ui.label().classes(
                "flex items-center px-3 text-xs font-medium text-[var(--text-muted)]"
            )
            if self._on_open_branches is not None:
                # color=None is the real fix for the "icon/label render in
                # Quasar's accent blue no matter what CSS says" issue —
                # ui.button() defaults color='primary' as an actual Quasar
                # prop, not just a class; no external stylesheet rule can
                # reliably out-cascade that without fighting it forever.
                ui.button(
                    "Branches", icon="account_tree", color=None, on_click=self._on_open_branches
                ).props("flat no-caps dense").classes("tau-topbar-tab")
            if self._on_toggle_files is not None:
                ui.button(
                    "Files", icon="folder_open", color=None, on_click=self._on_toggle_files
                ).props("flat no-caps dense").classes("tau-topbar-tab")
            self._stats_row = ui.row().classes(
                "items-center gap-3 px-3 ml-auto text-[11px] text-[var(--text-muted)] tau-topbar-stats"
            )

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
        if self._model_label is None or self._stats_row is None:
            return

        llm = self._runtime.agent._engine.llm if self._runtime.agent is not None else None
        if llm is not None:
            model_name = getattr(llm.model, "name", None) or getattr(llm.model, "id", "unknown")
            self._model_label.text = f"({llm.provider_id}) {model_name}"
        else:
            self._model_label.text = ""

        stats = _collect_session_stats(self._runtime)
        usage = self._runtime.agent.get_context_usage() if self._runtime.agent is not None else None

        self._stats_row.clear()
        with self._stats_row:
            if stats.input_tokens > 0:
                with ui.row().classes("items-center gap-1"):
                    ui.icon("arrow_upward").style("font-size: 12px;")
                    ui.label(_fmt_tokens(stats.input_tokens))
            if stats.output_tokens > 0:
                with ui.row().classes("items-center gap-1"):
                    ui.icon("arrow_downward").style("font-size: 12px;")
                    ui.label(_fmt_tokens(stats.output_tokens))
            if stats.cache_read_tokens > 0:
                with ui.row().classes("items-center gap-1"):
                    ui.icon("autorenew").style("font-size: 12px;")
                    ui.label(_fmt_tokens(stats.cache_read_tokens))
            if stats.cost > 0:
                ui.label(_fmt_cost(stats.cost)).classes("font-medium text-[var(--text)]")
            if usage is not None and usage.context_window > 0:
                pct = usage.percent or 0.0
                pct_label = f"{pct:.1f}%" if pct < 1 else f"{round(pct)}%"
                color = (
                    "#ef4444" if pct > 90 else "rgba(234,179,8,0.95)" if pct > 70 else "var(--text-muted)"
                )
                with ui.row().classes("items-center gap-1").style(f"color: {color} !important;"):
                    ui.icon("pie_chart").style("font-size: 12px;")
                    ui.label(f"{pct_label} / {_fmt_tokens(usage.context_window)}")
        tooltip_parts = []
        if stats.input_tokens:
            tooltip_parts.append(f"in: {stats.input_tokens:,}")
        if stats.output_tokens:
            tooltip_parts.append(f"out: {stats.output_tokens:,}")
        if stats.cache_read_tokens:
            tooltip_parts.append(f"cache read: {stats.cache_read_tokens:,}")
        if stats.cache_write_tokens:
            tooltip_parts.append(f"cache write: {stats.cache_write_tokens:,}")
        if stats.cost > 0:
            tooltip_parts.append(f"cost: ${stats.cost:.4f}")
        if tooltip_parts:
            self._stats_row.props(f'title="{"  |  ".join(tooltip_parts)}"')
