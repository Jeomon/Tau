from __future__ import annotations

import asyncio
from typing import Any

from component import _AskUserComponent  # type: ignore[import-not-found]
from schema import (  # type: ignore[import-not-found]
    AskUserParams,
    QuestionValidationError,
    normalize_options,
    validate_questions,
)

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
    questions = args.get("questions") or []
    if len(questions) <= 1:
        label = questions[0].get("question", "") if questions else ""
    else:
        label = f"{len(questions)} questions"
    return call_line("ask_user", label)


def _render_result(content: str, opts: Any) -> list[str]:
    return content.splitlines() or [content]


class AskUserTool(Tool):
    def __init__(self, runtime_ref: Any) -> None:
        self._runtime_ref = runtime_ref
        super().__init__(
            name="ask_user",
            description=(
                "Ask the human one or more focused questions and wait for their decision "
                "before proceeding. Pass multiple questions to run them as a sequence — "
                "each is shown and answered in turn, like a short interview — instead of "
                "issuing separate calls. Do not stack multiple ask_user calls back-to-back; "
                "group all clarifying questions into one invocation. Use for high-impact "
                "architectural trade-offs, ambiguous or conflicting requirements, or "
                "assumptions that would materially change the implementation. Each "
                "question supports single-select, multi-select, and freeform text "
                "answers, and up to 4 options when options are given. If you recommend a "
                'specific option, list it first and append "(Recommended)" to its '
                "title. Only available in an interactive TUI session."
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

        try:
            validate_questions(params.questions)
        except QuestionValidationError as e:
            return ToolResult.error(invocation.id, str(e))

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

        answers: list[dict] = []

        for question in params.questions:
            options = normalize_options(question.options)
            response = await self._ask_one(ui, question, options, params.timeout)

            if response is None:
                return ToolResult.ok(
                    invocation.id,
                    self._format_answers(answers, cancelled_at=question.question),
                    metadata={
                        "cancelled": True,
                        "answers": answers,
                        "cancelled_question": question.question,
                    },
                )

            if response["kind"] == "freeform":
                content = response["text"]
            else:
                content = ", ".join(response["selections"])

            answers.append({"question": question.question, "response": content, "raw": response})

        return ToolResult.ok(
            invocation.id,
            self._format_answers(answers),
            metadata={"cancelled": False, "answers": answers},
        )

    async def _ask_one(
        self,
        ui: Any,
        question: Any,
        options: Any,
        timeout: int | None,
    ) -> dict | None:
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
                question=question.question,
                context=question.context,
                options=options,
                allow_multiple=question.allow_multiple,
                allow_freeform=question.allow_freeform,
                multiline=question.multiline,
                on_done=lambda v: (_on_done(v), done(v)),
                theme=_theme,
            )
            if timeout:
                timeout_ms = timeout

                async def _auto_dismiss() -> None:
                    await asyncio.sleep(timeout_ms / 1000)
                    _on_done(None)
                    done(None)

                timeout_task_ref[0] = asyncio.ensure_future(_auto_dismiss())
            return component

        await ui.custom_inline(_factory, kind="ask_user")
        return await fut

    @staticmethod
    def _format_answers(answers: list[dict], cancelled_at: str | None = None) -> str:
        if not answers and cancelled_at is None:
            return "The user cancelled the question without answering."

        lines: list[str] = []
        for i, item in enumerate(answers, start=1):
            lines.append(f"Q{i}: {item['question']}")
            lines.append(f"A{i}: {item['response']}")
            lines.append("")

        if cancelled_at is not None:
            lines.append(f"(Cancelled before answering: {cancelled_at})")

        return "\n".join(lines).rstrip()
