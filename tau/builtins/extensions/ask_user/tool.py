from __future__ import annotations

import asyncio
from typing import Any

from component import _AskUserComponent
from schema import AskUserParams, normalize_options

from tau.tool.render import call_line
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    return call_line("ask_user", args.get("question", ""))


def _render_result(content: str, opts: Any) -> list[str]:
    return content.splitlines() or [content]


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
        options = normalize_options(params.options)

        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return ToolResult.error(invocation.id, "ask_user unavailable: runtime not ready")

        from tau.extensions.context import ExtensionContext

        ext_ctx = ExtensionContext.from_runtime(runtime)
        ui = ext_ctx.ui
        if ui is None:
            return ToolResult.error(
                invocation.id,
                "ask_user requires an interactive TUI session and is "
                "unavailable in headless/RPC mode",
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
