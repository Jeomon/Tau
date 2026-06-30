"""btw extension — parallel side-conversation channel.

Commands:
    /btw <question>           ask while the main agent is running
    /btw:new [question]       clear thread and start fresh
    /btw:tangent <question>   contextless side thread
    /btw:inject               send BTW thread back to main agent
    /btw:clear                hide the BTW panel and wipe the thread
"""

from __future__ import annotations

import asyncio
import textwrap
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.tui.component import Component

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext
    from tau.inference.api.text.service import TextLLM


# ── Widget component ───────────────────────────────────────────────────────────


class BtwWidget(Component):
    """Above-editor widget that shows the BTW transcript."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def set_lines(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def render(self, width: int) -> list[str]:
        from tau.modes.interactive.components.overlays import _box

        inner = list(self._lines) or ["  (empty)"]
        return _box(inner, "btw", width)

    def handle_input(self, event: Any) -> bool:
        return False  # never captures input — editor stays focused


# ── Per-session state ──────────────────────────────────────────────────────────


class BtwState:
    def __init__(self) -> None:
        self.thread: list[tuple[str, str]] = []  # (role, text)
        self._widget: BtwWidget | None = None
        self._request_render: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _rendered_lines(self, streaming: str = "") -> list[str]:
        lines: list[str] = []
        for role, text in self.thread:
            prefix = "\x1b[1myou\x1b[0m" if role == "user" else "\x1b[36mbtw\x1b[0m"
            lines.append(f"  {prefix}")
            for part in text.splitlines() or [""]:
                for wrapped in textwrap.wrap(part, width=60) or [part or ""]:
                    lines.append(f"    {wrapped}")
            lines.append("")
        if streaming:
            lines.append("  \x1b[36mbtw\x1b[0m \x1b[2m(thinking…)\x1b[0m")
            for part in streaming.splitlines() or [""]:
                for wrapped in textwrap.wrap(part, width=60) or [part or ""]:
                    lines.append(f"    {wrapped}")
        return lines

    def _refresh(self, streaming: str = "") -> None:
        if self._widget is None:
            return
        self._widget.set_lines(self._rendered_lines(streaming))
        if self._request_render is not None:
            self._request_render()

    # ── Widget lifecycle ──────────────────────────────────────────────────────

    def show_widget(self, ctx: ExtensionContext) -> None:
        ui = ctx.ui
        if ui is None:
            return
        if self._widget is None:
            self._widget = BtwWidget()
        self._request_render = ui.request_render
        self._widget.set_lines(self._rendered_lines())
        ui.set_widget("btw", self._widget, placement="above_editor")
        ui.request_render()

    def hide_widget(self, ctx: ExtensionContext) -> None:
        ui = ctx.ui
        if ui is not None:
            ui.remove_widget("btw")
            ui.request_render()
        self._widget = None
        self._request_render = None

    def clear(self, ctx: ExtensionContext | None = None) -> None:
        self._cancel_task()
        self.thread.clear()
        if ctx is not None:
            self.hide_widget(ctx)
        else:
            self._widget = None
            self._request_render = None

    # ── Task management ───────────────────────────────────────────────────────

    def _cancel_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def run(self, ctx: ExtensionContext, question: str, with_context: bool) -> None:
        self._cancel_task()
        self.thread.append(("user", question))
        self.show_widget(ctx)
        self._task = asyncio.ensure_future(self._stream(ctx, question, with_context))

        def _on_done(t: asyncio.Task[None]) -> None:
            if not t.cancelled() and (exc := t.exception()):
                import logging

                logging.getLogger(__name__).error("btw stream failed", exc_info=exc)

        self._task.add_done_callback(_on_done)

    async def _stream(self, ctx: ExtensionContext, question: str, with_context: bool) -> None:
        from tau.inference.api.text.service import TextLLM
        from tau.inference.types import LLMContext, TextDeltaEvent, TextEndEvent
        from tau.message.types import TextContent, UserMessage

        raw_llm = ctx.llm
        if raw_llm is None or not isinstance(raw_llm, TextLLM):
            self.thread.append(("btw", "(no model available)"))
            self._refresh()
            return

        llm: TextLLM = raw_llm
        messages = []

        if with_context:
            from tau.session.types import MessageEntry
            from tau.session.utils import to_llm_messages

            agent_msgs = [e.message for e in ctx.branch_entries if isinstance(e, MessageEntry)]
            messages.extend(to_llm_messages(agent_msgs))

        messages.append(UserMessage(contents=[TextContent(content=question)]))
        system_prompt = ctx.get_system_prompt() if with_context else None
        context = LLMContext(messages=messages, system_prompt=system_prompt)

        self._refresh(streaming=" ")

        response_text = ""
        try:
            async for event in llm.stream(context):
                if isinstance(event, TextDeltaEvent):
                    response_text += event.text.content
                    self._refresh(streaming=response_text)
                elif isinstance(event, TextEndEvent):
                    response_text = event.text.content
        except asyncio.CancelledError:
            if response_text:
                self.thread.append(("btw", response_text + " \x1b[2m[cancelled]\x1b[0m"))
            self._refresh()
            return

        self.thread.append(("btw", response_text or "(no response)"))
        self._refresh()


# ── Extension entry point ──────────────────────────────────────────────────────


def register(tau: ExtensionAPI) -> None:
    state: BtwState | None = None

    @tau.on("tui_ready")
    def _init(_event: Any, _ctx: Any) -> None:
        nonlocal state
        state = BtwState()

    @tau.on("session_start")
    def _reset(_event: Any, ctx: Any) -> None:
        nonlocal state
        if state is not None:
            state.clear(ctx if hasattr(ctx, "ui") else None)
        state = BtwState()

    # ── /btw <question> ──────────────────────────────────────────────────────

    async def cmd_btw(ctx: ExtensionContext, args: list[str]) -> None:
        nonlocal state
        if state is None:
            state = BtwState()
        question = " ".join(args).strip()
        if not question:
            ui = ctx.ui
            if ui is not None:
                ui.notify("Usage: /btw <question>")
            return
        state.run(ctx, question, with_context=True)

    tau.register_command(
        "btw",
        "Ask a side question (runs while main agent is busy)",
        cmd_btw,
        requires_idle=False,
    )

    # ── /btw:new [question] ──────────────────────────────────────────────────

    async def cmd_btw_new(ctx: ExtensionContext, args: list[str]) -> None:
        nonlocal state
        if state is not None:
            state.clear(ctx)
        state = BtwState()
        question = " ".join(args).strip()
        if question:
            state.run(ctx, question, with_context=True)
        else:
            state.show_widget(ctx)

    tau.register_command(
        "btw:new",
        "Clear BTW thread and start fresh",
        cmd_btw_new,
        requires_idle=False,
    )

    # ── /btw:tangent <question> ──────────────────────────────────────────────

    async def cmd_btw_tangent(ctx: ExtensionContext, args: list[str]) -> None:
        nonlocal state
        if state is None:
            state = BtwState()
        question = " ".join(args).strip()
        if not question:
            ui = ctx.ui
            if ui is not None:
                ui.notify("Usage: /btw:tangent <question>")
            return
        state.run(ctx, question, with_context=False)

    tau.register_command(
        "btw:tangent",
        "Ask a side question without inheriting session context",
        cmd_btw_tangent,
        requires_idle=False,
    )

    # ── /btw:inject ──────────────────────────────────────────────────────────

    async def cmd_btw_inject(ctx: ExtensionContext, args: list[str]) -> None:
        nonlocal state
        ui = ctx.ui
        if state is None or not state.thread:
            if ui is not None:
                ui.notify("No BTW thread to inject.")
            return

        parts = ["[btw thread]"]
        for role, text in state.thread:
            parts.append(f"{role}: {text}")
        message = "\n".join(parts)
        extra = " ".join(args).strip()
        if extra:
            message += f"\n\n{extra}"

        await ctx.send_user_message(message, deliver_as="follow_up", trigger_turn=True)
        state.clear(ctx)
        state = BtwState()
        if ui is not None:
            ui.notify("BTW thread injected into main agent.")

    tau.register_command(
        "btw:inject",
        "Send the BTW thread to the main agent as a follow-up",
        cmd_btw_inject,
        requires_idle=False,
    )

    # ── /btw:clear ───────────────────────────────────────────────────────────

    async def cmd_btw_clear(ctx: ExtensionContext, _args: list[str]) -> None:
        nonlocal state
        if state is not None:
            state.clear(ctx)
        state = BtwState()

    tau.register_command(
        "btw:clear",
        "Hide the BTW panel and clear the thread",
        cmd_btw_clear,
        requires_idle=False,
    )
