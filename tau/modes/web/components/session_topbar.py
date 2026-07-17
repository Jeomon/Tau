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

    Styled after pi-web's AppShell top bar (a tab-style Branches button on
    the left, a compact token/cost/context stats cluster on the right)
    rather than the plain text labels this used to be. There's no "Files"
    toggle here — browsing lives in the sidebar's always-visible Explorer
    tree, and the preview panel opens itself on demand from there.
    """

    def __init__(
        self,
        runtime: Runtime,
        *,
        dark_mode: ui.dark_mode,
        on_open_branches: Callable[[], None] | None = None,
        on_toggle_sidebar: Callable[[], bool] | None = None,
        on_toggle_file_panel: Callable[[], bool] | None = None,
    ) -> None:
        self._runtime = runtime
        self._dark_mode = dark_mode
        self._on_open_branches = on_open_branches
        self._on_toggle_sidebar = on_toggle_sidebar
        self._on_toggle_file_panel = on_toggle_file_panel
        self._sidebar_open = True
        self._file_panel_open = False
        self._stats_row: Any | None = None
        self._theme_button: Any | None = None
        self._sidebar_button: Any | None = None
        self._file_panel_button: Any | None = None

    def _theme_icon(self) -> str:
        return "dark_mode" if self._dark_mode.value else "light_mode"

    def _toggle_theme(self) -> None:
        self._dark_mode.value = not self._dark_mode.value
        if self._theme_button is not None:
            self._theme_button.props(f"icon={self._theme_icon()}")

    def _sidebar_icon(self) -> str:
        # "view_sidebar" (pi-web's own glyph concept) renders as a chunky,
        # hard-to-read block at 16px in Quasar's bundled classic Material
        # Icons font — menu_open/menu is the cleaner, more legible pairing
        # at this size and still reads clearly as an open/closed toggle.
        return "menu_open" if self._sidebar_open else "menu"

    def _toggle_sidebar(self) -> None:
        if self._on_toggle_sidebar is None:
            return
        self._sidebar_open = self._on_toggle_sidebar()
        if self._sidebar_button is not None:
            self._sidebar_button.props(f"icon={self._sidebar_icon()}")

    def _file_panel_icon(self) -> str:
        # menu_open mirrored horizontally reads naturally as "panel on the
        # right, opening/closing" — consistent with the sidebar's own icon
        # language instead of introducing a third icon family.
        return "menu_open" if self._file_panel_open else "menu"

    def _toggle_file_panel(self) -> None:
        if self._on_toggle_file_panel is not None:
            # Icon state is driven by sync_file_panel_open below, not this
            # return value — the panel can also open/close from outside this
            # button (picking a file in the sidebar), so there must be a
            # single source of truth that both paths feed into.
            self._on_toggle_file_panel()

    def sync_file_panel_open(self, open_: bool) -> None:
        """Mirror the file panel's actual visibility, however it changed."""
        self._file_panel_open = open_
        if self._file_panel_button is not None:
            self._file_panel_button.props(f"icon={self._file_panel_icon()}")

    def render(self) -> None:
        """Render the top bar and subscribe it to session-lifecycle events."""
        with ui.row().classes("w-full items-stretch gap-0 tau-topbar"):
            # Leftmost, matching pi-web's AppShell exactly — sidebar-toggle
            # first, theme-toggle second, both flat 36px icon buttons
            # bordered on the right.
            if self._on_toggle_sidebar is not None:
                self._sidebar_button = (
                    ui.button(icon=self._sidebar_icon(), color=None, on_click=self._toggle_sidebar)
                    .props("flat dense")
                    .classes("tau-topbar-icon-btn")
                )
            self._theme_button = (
                ui.button(icon=self._theme_icon(), color=None, on_click=self._toggle_theme)
                .props("flat dense")
                .classes("tau-topbar-icon-btn")
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
            self._stats_row = ui.row().classes(
                "items-center gap-3 px-3 ml-auto text-[11px] text-[var(--text-muted)] tau-topbar-stats"
            )
            if self._on_toggle_file_panel is not None:
                # Rightmost, mirroring the sidebar toggle's placement on the
                # far left — pi-web pins its own file-panel toggle at the
                # top-right corner (AppShell.tsx) for the same reason: always
                # reachable regardless of which tabs/panels are open.
                self._file_panel_button = (
                    ui.button(
                        icon=self._file_panel_icon(), color=None, on_click=self._toggle_file_panel
                    )
                    .props("flat dense")
                    .classes("tau-topbar-icon-btn tau-topbar-icon-btn-end")
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
        if self._stats_row is None:
            return

        stats = _collect_session_stats(self._runtime)
        usage = self._runtime.agent.get_context_usage() if self._runtime.agent is not None else None

        self._stats_row.clear()
        with self._stats_row:
            llm = self._runtime.agent._engine.llm if self._runtime.agent is not None else None
            if llm is not None:
                model_name = getattr(llm.model, "name", None) or getattr(llm.model, "id", "unknown")
                ui.label(f"({llm.provider_id}) {model_name}").classes(
                    "font-medium text-[var(--text)]"
                )
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
