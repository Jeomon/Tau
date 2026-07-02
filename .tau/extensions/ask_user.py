"""ask_user extension — interactive decision-gating tool for the agent.

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
    options: list[str | AskUserOption] | None = Field(
        default=None, description="Multiple-choice options"
    )
    allow_multiple: bool = Field(
        default=False, description="Allow selecting more than one option"
    )
    allow_freeform: bool = Field(
        default=True, description="Offer a 'Type something' freeform option"
    )
    multiline: bool = Field(
        default=False,
        description=(
            "Use a multi-line text editor for the freeform answer instead of a single "
            "line — set this for open-ended, long-form answers (e.g. 'write your bio', "
            "'describe the requirements'). Supports arrow-key navigation between lines, "
            "Enter for a newline, Ctrl+S/Ctrl+Enter to submit."
        ),
    )
    timeout: int | None = Field(
        default=None, description="Auto-dismiss after N ms and cancel if the prompt times out"
    )


AskUserParams.model_rebuild()


def _normalize_options(raw: list[str | AskUserOption] | None) -> list[AskUserOption]:
    return [AskUserOption(title=o) if isinstance(o, str) else o for o in (raw or [])]


# ── Rendering (tool call / result in the message list) ─────────────────────


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    return call_line("ask_user", args.get("question", ""))


def _render_result(content: str, opts: Any) -> list[str]:
    return content.splitlines() or [content]


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

    ML_VISIBLE_ROWS = 8
    ML_MIN_VISIBLE_ROWS = 4  # pad short buffers so the box visibly reads as multi-line

    def __init__(
        self,
        question: str,
        context: str | None,
        options: list[AskUserOption],
        allow_multiple: bool,
        allow_freeform: bool,
        multiline: bool,
        on_done: Any,
    ) -> None:
        self._question = question
        self._context = context
        self._options = options
        self._allow_multiple = allow_multiple
        self._allow_freeform = allow_freeform
        self._multiline = multiline
        self._on_done = on_done

        self._cursor = 0
        self._checked: set[int] = set()
        self._mode = "list"  # "list" | "freeform"
        self._freeform_value = ""  # single-line freeform buffer

        # Multi-line freeform buffer (used only when self._multiline).
        self._ml_lines: list[str] = [""]
        self._ml_cursor_row = 0
        self._ml_cursor_col = 0
        self._ml_scroll_top = 0

        # Index of the synthetic "Type something…" row, if present.
        self._freeform_index = len(options) if allow_freeform else -1
        self._row_count = len(options) + (1 if allow_freeform else 0)

        # Multi-line with no real choices — freeform is the only path and there's
        # a whole editor box to show anyway, so open straight into it instead of
        # forcing an Enter on a single "Type something…" row first. Single-line
        # freeform keeps the selector screen — it's a cheap, expected step there.
        if not self._options and self._allow_freeform and self._multiline:
            self._enter_freeform()

    def _enter_freeform(self, seed: str = "") -> None:
        self._mode = "freeform"
        if self._multiline:
            self._ml_lines = [seed]
            self._ml_cursor_row = 0
            self._ml_cursor_col = len(seed)
            self._ml_scroll_top = 0
        else:
            self._freeform_value = seed

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

        if self._mode == "freeform" and self._multiline:
            self._clamp_ml_scroll()
            visible = self._ml_lines[self._ml_scroll_top : self._ml_scroll_top + self.ML_VISIBLE_ROWS]
            for ri, line in enumerate(visible):
                abs_row = self._ml_scroll_top + ri
                if abs_row == self._ml_cursor_row:
                    before = line[: self._ml_cursor_col]
                    after = line[self._ml_cursor_col :]
                    inner.append(f"  {before}█{after}")
                else:
                    inner.append(f"  {line}")
            # Pad with blank rows (display-only, not part of the buffer) so an
            # empty/short answer still shows several rows — a visible cue that
            # this is a multi-line editor, not a single-line box.
            for _ in range(self.ML_MIN_VISIBLE_ROWS - len(visible)):
                inner.append("")
            total = len(self._ml_lines)
            if total > self.ML_VISIBLE_ROWS:
                pct = int(self._ml_scroll_top / max(1, total - self.ML_VISIBLE_ROWS) * 100)
                inner.append(f"  \x1b[2m↕ {pct}%\x1b[0m")
            else:
                inner.append("")
            inner.append(
                "  \x1b[2mEnter to submit  ·  \\+Enter or Shift+Enter for newline  ·  "
                "Esc to go back\x1b[0m"
            )
            return _box(inner, "", width, bg="")

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
                # Same tick glyphs as the /extensions config panel. Cursor rows
                # get wrapped in reverse-video below, so leave them uncolored
                # there — an embedded reset would cut the highlight short.
                if i in self._checked:
                    box = "✔" if is_cursor else "\x1b[32m✔\x1b[0m"
                else:
                    box = "✖" if is_cursor else "\x1b[2m✖\x1b[0m"
            elif not is_freeform_row:
                # Radio-style circles: filled at the cursor position, hollow
                # elsewhere. Same reverse-video caveat as the checkbox ticks
                # above — leave the cursor row's glyph uncolored.
                box = "●" if is_cursor else "\x1b[2m○\x1b[0m"
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
                    self._enter_freeform()
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
                self._enter_freeform(seed=_typed_char(event) or "")
            case _:
                return False
        return True

    def _handle_freeform_input(self, event: KeyEvent) -> bool:
        if self._multiline:
            return self._handle_multiline_input(event)

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

    def _ml_insert_newline(self) -> None:
        line = self._ml_lines[self._ml_cursor_row]
        before, after = line[: self._ml_cursor_col], line[self._ml_cursor_col :]
        self._ml_lines[self._ml_cursor_row] = before
        self._ml_lines.insert(self._ml_cursor_row + 1, after)
        self._ml_cursor_row += 1
        self._ml_cursor_col = 0

    def _handle_multiline_input(self, event: KeyEvent) -> bool:
        k = event.key

        if k == "escape":
            if not self._options:
                self._on_done(None)
            else:
                self._mode = "list"
            return True
        if k == "enter":
            # Shift+Enter always inserts a newline. Ctrl+S/Ctrl+Enter aren't
            # reliable across terminals (Ctrl+S is XOFF flow control in many),
            # so plain Enter submits — unless the line ends with a trailing
            # "\", a shell-style continuation marker meaning "newline, please".
            if event.shift:
                self._ml_insert_newline()
                return True
            line = self._ml_lines[self._ml_cursor_row]
            if self._ml_cursor_col > 0 and line[self._ml_cursor_col - 1] == "\\":
                self._ml_lines[self._ml_cursor_row] = (
                    line[: self._ml_cursor_col - 1] + line[self._ml_cursor_col :]
                )
                self._ml_cursor_col -= 1
                self._ml_insert_newline()
                return True
            self._on_done({"kind": "freeform", "text": "\n".join(self._ml_lines)})
            return True
        if k == "backspace":
            if self._ml_cursor_col > 0:
                line = self._ml_lines[self._ml_cursor_row]
                self._ml_lines[self._ml_cursor_row] = (
                    line[: self._ml_cursor_col - 1] + line[self._ml_cursor_col :]
                )
                self._ml_cursor_col -= 1
            elif self._ml_cursor_row > 0:
                prev = self._ml_lines[self._ml_cursor_row - 1]
                merged = prev + self._ml_lines.pop(self._ml_cursor_row)
                self._ml_cursor_row -= 1
                self._ml_cursor_col = len(prev)
                self._ml_lines[self._ml_cursor_row] = merged
            return True
        if k == "up":
            if self._ml_cursor_row > 0:
                self._ml_cursor_row -= 1
                self._ml_cursor_col = min(self._ml_cursor_col, len(self._ml_lines[self._ml_cursor_row]))
            return True
        if k == "down":
            if self._ml_cursor_row < len(self._ml_lines) - 1:
                self._ml_cursor_row += 1
                self._ml_cursor_col = min(self._ml_cursor_col, len(self._ml_lines[self._ml_cursor_row]))
            return True
        if k == "left":
            if self._ml_cursor_col > 0:
                self._ml_cursor_col -= 1
            elif self._ml_cursor_row > 0:
                self._ml_cursor_row -= 1
                self._ml_cursor_col = len(self._ml_lines[self._ml_cursor_row])
            return True
        if k == "right":
            line = self._ml_lines[self._ml_cursor_row]
            if self._ml_cursor_col < len(line):
                self._ml_cursor_col += 1
            elif self._ml_cursor_row < len(self._ml_lines) - 1:
                self._ml_cursor_row += 1
                self._ml_cursor_col = 0
            return True
        if k == "home":
            self._ml_cursor_col = 0
            return True
        if k == "end":
            self._ml_cursor_col = len(self._ml_lines[self._ml_cursor_row])
            return True
        ch = _typed_char(event)
        if ch is not None:
            line = self._ml_lines[self._ml_cursor_row]
            self._ml_lines[self._ml_cursor_row] = (
                line[: self._ml_cursor_col] + ch + line[self._ml_cursor_col :]
            )
            self._ml_cursor_col += len(ch)
            return True
        return False

    def _clamp_ml_scroll(self) -> None:
        if self._ml_cursor_row < self._ml_scroll_top:
            self._ml_scroll_top = self._ml_cursor_row
        elif self._ml_cursor_row >= self._ml_scroll_top + self.ML_VISIBLE_ROWS:
            self._ml_scroll_top = self._ml_cursor_row - self.ML_VISIBLE_ROWS + 1
        # Pull the window back down when lines below it are deleted (e.g. via
        # backspace-merge) — otherwise the visible slice shrinks below
        # ML_VISIBLE_ROWS even while there's still enough content to fill it.
        max_scroll_top = max(0, len(self._ml_lines) - self.ML_VISIBLE_ROWS)
        self._ml_scroll_top = max(0, min(self._ml_scroll_top, max_scroll_top))

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
            render_shell="default",
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
                multiline=params.multiline,
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
            CustomOptions(overlay_options=OverlayOptions(width="70%", anchor="center", margin=1)),
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
