"""autoresearch — an autonomous optimisation loop for tau.

Try an idea, measure it, keep what works, revert what doesn't, repeat.

The extension is deliberately domain-agnostic: it knows how to run a command,
read a number out of it, record the decision, and draw the result. *What* to
optimise — the benchmark, the metric, the ideas worth trying — lives in
``.auto/prompt.md`` and the ``autoresearch-create`` skill. One extension, any
optimisation target.

Ported from davebcn87/pi-autoresearch, itself inspired by karpathy/autoresearch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect

from .dashboard import DashboardOverlay, widget_lines
from .state import (
    State,
    clear,
    load,
    log_path,
    measure_path,
    prompt_path,
)
from .tools import build_tools

WIDGET_KEY = "autoresearch"
FULLSCREEN_KEY = "ctrl+shift+f"


class _Widget(Component):
    """Line-based block above the editor; content is pushed in by the Session.

    Must be a real ``Component`` — ``set_widget`` renders it through the
    component tree, and a duck-typed object fails at paint time with the
    exception swallowed into the session log.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []

    def set_lines(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        row = 0
        for line in self._lines:
            row += parse_ansi_wrapped_into(buf, area.x, area.y + row, line, area.width)
        return row


class Session:
    """Live session state plus everything needed to redraw it.

    Tools hold a reference to this rather than to the UI, so they stay usable
    in headless runs where there is no widget to update.
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.state: State = load(cwd)
        #: Set by `/autoresearch off`. Everything else is derived from disk, so
        #: a session started after launch (or by another process) still shows.
        self.dismissed = False
        self._ctx: Any = None
        self._widget: _Widget | None = None
        self._shown = False

    @property
    def active(self) -> bool:
        """Show the dashboard when there is a session and it was not dismissed."""
        if self.dismissed:
            return False
        return bool(self.state.results) or prompt_path(self.cwd).exists()

    # ── Wiring ────────────────────────────────────────────────────────────

    def bind(self, ctx: Any) -> None:
        self._ctx = ctx

    def reload(self) -> None:
        """Re-read the log from disk — used after an external edit or a clear."""
        self.state = load(self.cwd)

    # ── Rendering ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Push current state into the widget (no-op without a TUI)."""
        ctx = self._ctx
        ui = getattr(ctx, "ui", None) if ctx is not None else None
        if ui is None or getattr(ui, "theme", None) is None:
            return

        if not self.active:
            self.hide()
            return

        if self._widget is None:
            self._widget = _Widget()
        self._widget.set_lines(widget_lines(self.state, ui.theme, self._width()))
        if not self._shown:
            ui.set_widget(WIDGET_KEY, self._widget, placement="above_editor")
            self._shown = True
        else:
            ui.request_render()

    #: The render shell indents every widget line ("     └ " then 9 spaces of
    #: hanging indent), so content must be laid out narrower than the terminal
    #: or full-width rules wrap onto a second row.
    SHELL_INDENT = 12

    def _width(self) -> int:
        """Usable content width inside the widget's render shell."""
        ui = getattr(self._ctx, "ui", None) if self._ctx is not None else None
        layout = getattr(ui, "_layout", None)
        tui = getattr(layout() if callable(layout) else layout, "_tui", None)
        width = getattr(getattr(tui, "_terminal", None), "width", None)
        if not isinstance(width, int) or width <= 0:
            return 100
        return max(40, width - self.SHELL_INDENT)

    def hide(self) -> None:
        ui = getattr(self._ctx, "ui", None) if self._ctx is not None else None
        if ui is not None and self._shown:
            ui.remove_widget(WIDGET_KEY)
        self._shown = False
        self._widget = None

    def open_overlay(self) -> None:
        ui = getattr(self._ctx, "ui", None) if self._ctx is not None else None
        if ui is None or not getattr(ui, "supports_components", True):
            return
        theme = ui.theme
        if theme is None:
            return

        handle: dict[str, Any] = {}

        def _close() -> None:
            entry = handle.get("handle")
            if entry is not None:
                entry.close()

        overlay = DashboardOverlay(self.state, theme, _close)
        handle["handle"] = ui.show_overlay(overlay, width="94%", max_height="90%")


# ── Command ───────────────────────────────────────────────────────────────────


_HELP = """autoresearch — autonomous optimisation loop

  /autoresearch <goal>      start or resume a session with <goal> as context
  /autoresearch status      show where the session stands
  /autoresearch dashboard   open the fullscreen, scrollable view
  /autoresearch off         hide the dashboard, keep the log
  /autoresearch clear       delete .auto/log.jsonl and reset

Files live in .auto/ — prompt.md (the session document), measure.sh (the
benchmark), log.jsonl (every run). ctrl+shift+f opens the fullscreen view too,
where the terminal delivers that combination."""


def _notify(ctx: Any, message: str) -> None:
    """Post a message to the transcript.

    ``ExtensionContext`` has no ``notify`` of its own — it lives on ``ctx.ui``,
    which is ``None`` in print/JSON mode. Commands can be invoked there, so the
    call has to be guarded rather than assumed.
    """
    ui = getattr(ctx, "ui", None)
    if ui is not None:
        ui.notify(message)


def _status_text(session: Session) -> str:
    state = session.state
    if not state.results:
        return "No experiments logged yet. Run `/skill:autoresearch-create` to set one up."
    counts = state.counts()
    best = state.best()
    baseline = state.baseline()
    lines = [
        f"{state.name or 'autoresearch'} — {len(state.current())} runs "
        f"({counts['keep']} kept, {counts['discard']} discarded, "
        f"{counts['crash']} crashed, {counts['checks_failed']} checks failed)"
    ]
    if baseline is not None and best is not None:
        from .state import format_num, is_better  # noqa: PLC0415

        delta = ""
        if baseline.metric:
            pct = (best.metric - baseline.metric) / baseline.metric * 100
            arrow = (
                "better" if is_better(best.metric, baseline.metric, state.direction) else "worse"
            )
            delta = f" ({pct:+.1f}% {arrow})"
        lines.append(
            f"{state.metric_name}: baseline {format_num(baseline.metric, state.metric_unit)} "
            f"→ best {format_num(best.metric, state.metric_unit)}{delta}"
        )
    confidence = state.confidence()
    if confidence is not None:
        lines.append(f"Confidence: {confidence:.1f}× the session noise floor")
    return "\n".join(lines)


def register(tau: Any) -> None:
    cwd = Path.cwd()
    session = Session(cwd)

    for tool in build_tools(session):
        tau.register_tool(tool)

    @tau.on("tui_ready")
    def _on_ready(_event: Any, ctx: Any) -> None:
        session.bind(ctx)
        session.refresh()

    @tau.on("settled")
    def _on_settled(_event: Any, ctx: Any) -> None:
        # The agent may have appended results through the tools; keep the
        # dashboard honest after every turn.
        session.bind(ctx)
        session.refresh()

    async def _command(ctx: Any, args: list[str]) -> None:
        session.bind(ctx)
        sub = args[0].lower() if args else ""

        if sub in ("help", "-h", "--help"):
            _notify(ctx, _HELP)
            return

        if sub in ("dashboard", "full"):
            session.reload()
            session.open_overlay()
            return

        if sub == "status":
            session.reload()
            session.refresh()
            _notify(ctx, _status_text(session))
            return

        if sub == "off":
            session.dismissed = True
            session.hide()
            _notify(ctx, "autoresearch: dashboard off. The log in .auto/ is untouched.")
            return

        if sub == "clear":
            clear(session.cwd)
            session.reload()
            session.dismissed = True
            session.hide()
            _notify(ctx, "autoresearch: log cleared. prompt.md and measure.sh were kept.")
            return

        # Anything else is a goal: start or resume.
        session.reload()
        session.dismissed = False
        session.refresh()

        goal = " ".join(args).strip()
        if prompt_path(session.cwd).exists():
            note = f"Resuming autoresearch from {prompt_path(session.cwd)}."
            if goal:
                note += f"\nAdded context: {goal}"
            _notify(ctx, note)
            await ctx.send_message(
                "Continue the autoresearch loop. Re-read .auto/prompt.md, the tail of "
                ".auto/log.jsonl and `git log` first so you are working from the files "
                "rather than memory, then run the next experiment."
                + (f"\n\nExtra context from the user: {goal}" if goal else "")
            )
            return

        _notify(
            ctx,
            "autoresearch: no .auto/prompt.md yet — setting up a new session." if goal else _HELP,
        )
        if goal:
            await ctx.send_message(
                "Set up an autoresearch session with the autoresearch-create skill. "
                f"The user's goal: {goal}\n\n"
                f"Write {prompt_path(session.cwd)} and {measure_path(session.cwd)}, call "
                "init_experiment, run the baseline, then start the loop."
            )

    tau.register_command(
        "autoresearch",
        "Autonomous optimisation loop — start, resume, or inspect a session",
        _command,
        argument_hint="<goal> | status | dashboard | off | clear",
    )

    @tau.register_shortcut(FULLSCREEN_KEY, "autoresearch: fullscreen dashboard")
    def _fullscreen(ctx: Any) -> None:
        session.bind(ctx)
        session.reload()
        session.open_overlay()

    # Surface the log path for other extensions / debugging.
    tau.provide("autoresearch", {"session": session, "log_path": log_path(cwd)})
