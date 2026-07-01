"""ask_user extension — interactive decision-gating tool for the agent.

Registers an `ask_user` tool the agent can call to pause and collect a
structured decision (single/multi-select, optional freeform) from the human
via a floating TUI overlay. Modeled on the `ask_user` tool shipped by the
`pi-ask-user` package (https://github.com/edlsh/pi-ask-user), adapted to
Tau's own overlay/component primitives.

Outside an interactive TUI session (headless / RPC mode) the tool returns a
clear error instead of hanging, since there is nowhere to render the prompt.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from tau.tool.render import call_line
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent

# ── Schema ───────────────────────────────────────────────────────────────────


class AskUserOption(BaseModel):
    title: str
    description: str | None = None


class AskUserParams(BaseModel):
    question: str = Field(..., description="The question to ask the user")
    context: str | None = Field(
        default=None, description="Relevant context summary shown before the question"
    )
    options: list[str | AskUserOption] = Field(
        default_factory=list, description="Multiple-choice options"
    )
    allow_multiple: bool = Field(
        default=False, description="Allow selecting more than one option"
    )
    allow_freeform: bool = Field(
        default=True, description="Offer a 'Type something' freeform option"
    )
    timeout: int | None = Field(
        default=None, description="Auto-dismiss after N ms and cancel if the prompt times out"
    )


AskUserParams.model_rebuild()


def _normalize_options(raw: list[str | AskUserOption]) -> list[AskUserOption]:
    return [AskUserOption(title=o) if isinstance(o, str) else o for o in raw]


# ── Rendering (tool call / result in the message list) ─────────────────────


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    return call_line("ask_user", args.get("question", ""))


def _render_result(content: str, opts: Any) -> list[str]:
    return [f"  {content}"]


# ── Interactive component ───────────────────────────────────────────────────

FREEFORM_LABEL = "Type something…"


def _typed_char(event: KeyEvent) -> str | None:
    """Return the printable character a key event represents, or None.

    ``event.key`` is always lowercased by the parser (the original case lives
    in ``event.char``), so text entry must read ``char`` to avoid silently
    lowercasing everything the user types.
    """
    if event.char is not None and len(event.char) >= 1 and event.char.isprintable():
        return event.char
    if len(event.key) == 1 and event.key.isprintable():
        return event.key
    return None


class _AskUserComponent(Component):
    """Floating dialog: option list (single/multi-select) + optional freeform entry."""

    def __init__(
        self,
        question: str,
        context: str | None,
        options: list[AskUserOption],
        allow_multiple: bool,
        allow_freeform: bool,
        on_done: Any,
    ) -> None:
        self._question = question
        self._context = context
        self._options = options
        self._allow_multiple = allow_multiple
        self._allow_freeform = allow_freeform
        self._on_done = on_done

        self._cursor = 0
        self._checked: set[int] = set()
        self._mode = "list"  # "list" | "freeform"
        self._freeform_value = ""

        # Index of the synthetic "Type something…" row, if present.
        self._freeform_index = len(options) if allow_freeform else -1
        self._row_count = len(options) + (1 if allow_freeform else 0)

    # ── Render ────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        from tau.modes.interactive.components.overlays import _box

        inner: list[str] = []
        if self._context:
            for line in self._context.splitlines():
                inner.append(f"  \x1b[2m{line}\x1b[0m")
            inner.append("")
        inner.append(f"  \x1b[1m{self._question}\x1b[0m")
        inner.append("")

        if self._mode == "freeform":
            inner.append(f"  {self._freeform_value}█")
            inner.append("")
            inner.append("  \x1b[2mEnter to submit  ·  Esc to go back\x1b[0m")
            return _box(inner, "", width, bg="")

        for i in range(self._row_count):
            is_freeform_row = i == self._freeform_index
            title = FREEFORM_LABEL if is_freeform_row else self._options[i].title
            desc = "" if is_freeform_row else (self._options[i].description or "")
            is_cursor = i == self._cursor
            cursor_mark = "›" if is_cursor else " "

            if self._allow_multiple and not is_freeform_row:
                box = "[x]" if i in self._checked else "[ ]"
            elif not is_freeform_row:
                box = "(o)" if is_cursor else "( )"
            else:
                box = " > "

            row = f"  {cursor_mark} {box} {title}"
            if is_cursor:
                row = f"\x1b[7m{row}\x1b[0m"
            if desc:
                row += f"  \x1b[2m{desc}\x1b[0m"
            inner.append(row)

        inner.append("")
        hints = ["↑/↓ move", "Enter confirm", "Esc cancel"]
        if self._allow_multiple:
            hints.insert(1, "Space toggle")
        inner.append("  \x1b[2m" + "  ·  ".join(hints) + "\x1b[0m")
        return _box(inner, "", width)

    # ── Input ─────────────────────────────────────────────────────────────

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        if self._mode == "freeform":
            return self._handle_freeform_input(event)
        return self._handle_list_input(event)

    def _handle_list_input(self, event: KeyEvent) -> bool:
        match event.key:
            case "up":
                self._cursor = (self._cursor - 1) % max(1, self._row_count)
            case "down":
                self._cursor = (self._cursor + 1) % max(1, self._row_count)
            case " " | "space" if self._allow_multiple and self._cursor != self._freeform_index:
                if self._cursor in self._checked:
                    self._checked.discard(self._cursor)
                else:
                    self._checked.add(self._cursor)
            case "enter":
                if self._cursor == self._freeform_index:
                    self._mode = "freeform"
                elif self._allow_multiple:
                    chosen = self._checked or {self._cursor}
                    self._on_done(
                        {
                            "kind": "selection",
                            "selections": [self._options[i].title for i in sorted(chosen)],
                        }
                    )
                else:
                    self._on_done(
                        {"kind": "selection", "selections": [self._options[self._cursor].title]}
                    )
            case "escape":
                self._on_done(None)
            case _ if self._allow_freeform and _typed_char(event) is not None:
                # Typing directly on the list view jumps straight into freeform
                # entry instead of requiring the user to navigate to and Enter
                # the "Type something…" row first.
                self._mode = "freeform"
                self._freeform_value = _typed_char(event) or ""
            case _:
                return False
        return True

    def _handle_freeform_input(self, event: KeyEvent) -> bool:
        match event.key:
            case "enter":
                self._on_done({"kind": "freeform", "text": self._freeform_value})
            case "escape":
                self._mode = "list"
                self._freeform_value = ""
            case "backspace":
                self._freeform_value = self._freeform_value[:-1]
            case _ if _typed_char(event) is not None:
                self._freeform_value += _typed_char(event) or ""
            case _:
                return False
        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: Any) -> None:
        pass


# ── Tool ─────────────────────────────────────────────────────────────────────


class AskUserTool(Tool):
    def __init__(self, runtime_ref: Any) -> None:
        self._runtime_ref = runtime_ref
        super().__init__(
            name="ask_user",
            description=(
                "Ask the human a focused question and wait for their decision before "
                "proceeding. Use for high-impact architectural trade-offs, ambiguous or "
                "conflicting requirements, or assumptions that would materially change "
                "the implementation. Supports single-select, multi-select, and freeform "
                "text answers. Only available in an interactive TUI session."
            ),
            schema=AskUserParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=_render_call,
            render_result=_render_result,
            render_shell="self",
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = AskUserParams.model_validate(invocation.params)
        options = _normalize_options(params.options)

        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return ToolResult.error(invocation.id, "ask_user unavailable: runtime not ready")

        from tau.extensions.context import ExtensionContext

        ext_ctx = ExtensionContext.from_runtime(runtime)
        ui = ext_ctx.ui
        if ui is None:
            return ToolResult.error(
                invocation.id,
                "ask_user requires an interactive TUI session and is unavailable in headless/RPC mode",
            )

        from tau.tui.tui import CustomOptions, OverlayOptions

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict | None] = loop.create_future()
        timeout_task_ref: list[asyncio.Task | None] = [None]

        def _on_done(value: dict | None) -> None:
            t = timeout_task_ref[0]
            if t is not None and not t.done():
                t.cancel()
            if not fut.done():
                fut.set_result(value)

        def _factory(_tui, _theme, _kb, done):
            component = _AskUserComponent(
                question=params.question,
                context=params.context,
                options=options,
                allow_multiple=params.allow_multiple,
                allow_freeform=params.allow_freeform,
                on_done=lambda v: (_on_done(v), done(v)),
            )
            timeout_ms = params.timeout
            if timeout_ms:

                async def _auto_dismiss() -> None:
                    await asyncio.sleep(timeout_ms / 1000)
                    _on_done(None)
                    done(None)

                timeout_task_ref[0] = asyncio.ensure_future(_auto_dismiss())
            return component

        await ui.custom(
            _factory,
            CustomOptions(overlay_options=OverlayOptions(width="70%", anchor="center")),
        )
        response = await fut

        if response is None:
            return ToolResult.ok(
                invocation.id,
                "The user cancelled the question without answering.",
                metadata={"cancelled": True, "question": params.question},
            )

        if response["kind"] == "freeform":
            content = response["text"]
        else:
            content = ", ".join(response["selections"])

        return ToolResult.ok(
            invocation.id,
            content,
            metadata={"cancelled": False, "question": params.question, "response": response},
        )


# ── Registration ─────────────────────────────────────────────────────────────


def register(tau: Any) -> None:
    tau.register_tool(AskUserTool(tau._runtime_ref))
