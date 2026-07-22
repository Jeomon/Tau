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
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect


# ── Widget component ───────────────────────────────────────────────────────────


class BtwWidget(Component):
    """Above-editor widget that shows the BTW transcript."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def set_lines(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.modes.interactive.components.overlays import _box_cells
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into
        from tau.tui.buffer import Buffer as _Buffer
        from tau.tui.geometry import Rect as _Rect

        inner_w = max(1, area.width - 4)
        inner = _Buffer.empty(_Rect(0, 0, inner_w, 0))
        lines = list(self._lines) or ["  (empty)"]
        row = 0
        for line in lines:
            row += parse_ansi_wrapped_into(inner, 0, row, line, inner_w)

        return _box_cells(buf, area, inner, row, "btw", None)

    def handle_input(self, event: Any) -> bool:
        return False  # never captures input — editor stays focused


# Appended to the main agent's system prompt for contextual side questions.
# Without it, a model conditioned on a tool-using conversation keeps trying to
# answer with a tool call — and with no tools available in this channel, that
# surfaces as an empty response (verified live against claude-sonnet-5).
_SIDE_CHANNEL_PROMPT = (
    "\n\n[btw side-channel]\n"
    "This request is a parallel side conversation, separate from the main agent session "
    "shown above. The main task is being handled by another agent — do not continue it. "
    "You have NO tools in this side channel: every tool described earlier is unavailable, "
    "and you must never emit a tool call. Answer the user's side question directly in "
    "plain text, using the conversation so far only as background context. If the answer "
    "requires tools or live data you don't have, say so briefly and give your best "
    "text-only answer."
)


# ── Per-session state ──────────────────────────────────────────────────────────


class BtwState:
    #: Content width the transcript is laid out at before the widget re-wraps
    #: it into the box; matches the old textwrap width so the look is stable.
    _WRAP_WIDTH = 60

    def __init__(self) -> None:
        self.thread: list[tuple[str, str]] = []  # (role, text)
        self._widget: BtwWidget | None = None
        self._request_render: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._md_theme: Any = None  # MarkdownTheme, captured from the UI

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _plain_lines(self, text: str) -> list[str]:
        out: list[str] = []
        for part in text.splitlines() or [""]:
            for wrapped in textwrap.wrap(part, width=self._WRAP_WIDTH) or [part or ""]:
                out.append(f"    {wrapped}")
        return out

    def _assistant_lines(self, text: str) -> list[str]:
        """Assistant replies are markdown — render them like the main chat.

        Falls back to plain wrapping when no theme is available (headless) or
        the renderer chokes on a half-streamed document.
        """
        if self._md_theme is not None:
            try:
                from tau.tui.markdown import render_markdown

                rendered = render_markdown(text, self._WRAP_WIDTH, self._md_theme)
                return [f"    {line}" for line in rendered]
            except Exception:
                pass
        return self._plain_lines(text)

    def _rendered_lines(self, streaming: str = "") -> list[str]:
        lines: list[str] = []
        for role, text in self.thread:
            if role == "user":
                lines.append("  \x1b[1mYou\x1b[0m")
                lines.extend(self._plain_lines(text))
            else:
                lines.append("  \x1b[36mAssistant\x1b[0m")
                lines.extend(self._assistant_lines(text))
            lines.append("")
        if streaming:
            lines.append("  \x1b[36mAssistant\x1b[0m \x1b[2m(thinking…)\x1b[0m")
            lines.extend(self._assistant_lines(streaming))
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
        # Re-capture per show so a /theme switch is picked up next time.
        # MarkdownTheme lives at LayoutTheme.message.markdown.
        message_theme = getattr(getattr(ui, "theme", None), "message", None)
        self._md_theme = getattr(message_theme, "markdown", None)
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
        from tau.inference.types import (
            ErrorEvent,
            LLMContext,
            RetryEvent,
            TextDeltaEvent,
            TextEndEvent,
        )
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
            # Dangling tool calls in a mid-turn branch are repaired downstream
            # by TextLLM._resolve_messages (close_dangling_tool_calls).
            messages.extend(to_llm_messages(agent_msgs))

        messages.append(UserMessage(contents=[TextContent(content=question)]))
        system_prompt = ctx.get_system_prompt() + _SIDE_CHANNEL_PROMPT if with_context else None
        context = LLMContext(messages=messages, system_prompt=system_prompt)

        self._refresh(streaming=" ")

        response_text = ""
        error_text = ""
        try:
            async for event in llm.stream(context):
                if isinstance(event, TextDeltaEvent):
                    response_text += event.text.content
                    self._refresh(streaming=response_text)
                elif isinstance(event, TextEndEvent):
                    response_text = event.text.content
                elif isinstance(event, RetryEvent):
                    self._refresh(
                        streaming=f"\x1b[2m(retrying {event.attempt}/{event.max_retries}:"
                        f" {event.error})\x1b[0m"
                    )
                elif isinstance(event, ErrorEvent):
                    error_text = event.error or "stream error"
        except asyncio.CancelledError:
            if response_text:
                self.thread.append(("btw", response_text + " \x1b[2m[cancelled]\x1b[0m"))
            self._refresh()
            return
        except Exception as e:
            import logging

            logging.getLogger(__name__).error("btw stream failed", exc_info=e)
            error_text = str(e) or type(e).__name__

        if response_text:
            self.thread.append(("btw", response_text))
        elif error_text:
            self.thread.append(("btw", f"\x1b[31m(error: {error_text})\x1b[0m"))
        else:
            self.thread.append(("btw", "(no response)"))
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
