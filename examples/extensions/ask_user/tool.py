from __future__ import annotations

import asyncio
from typing import Any

from component import _AskUserComponent, _AskUserSequence
from rpc_backend import ask_over_bridge
from schema import (
    MAX_HEADER_LENGTH,
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


def _header_for(question: Any, index: int) -> str:
    """Tab label: the model's own ``header``, else a truncated question."""
    if question.header:
        return question.header.strip()[:MAX_HEADER_LENGTH]
    text = " ".join(question.question.split())
    if len(text) <= MAX_HEADER_LENGTH:
        return text or f"Q{index + 1}"
    return text[: MAX_HEADER_LENGTH - 1] + "…"


def _disable_tool(runtime: Any, name: str) -> bool:
    """Drop one tool from the running agent so the LLM stops offering it.

    Used when the tool cannot possibly work for the rest of the session (no
    interactive UI). Mirrors ``ExtensionAPI.set_active_tools`` but removes a
    single tool instead of replacing the whole allowlist, so it can't
    resurrect tools another extension disabled.
    """
    engine = getattr(getattr(runtime, "agent", None), "_engine", None)
    if engine is None:
        return False
    tools = [t for t in getattr(engine, "tools", []) if t.name != name]
    if len(tools) == len(getattr(engine, "tools", [])):
        return False
    engine.tools = tools
    engine._tools = {t.name: t for t in tools}
    return True


class AskUserTool(Tool):
    def __init__(self, runtime_ref: Any) -> None:
        self._runtime_ref = runtime_ref
        super().__init__(
            name="ask_user",
            description=(
                "Ask the human one or more focused questions and wait for their decision "
                "before proceeding. Pass multiple questions to show them together as tabs "
                "the user can move between, revise, and submit in one go — instead of "
                "issuing separate calls. Do not stack multiple ask_user calls back-to-back; "
                "group all clarifying questions into one invocation. Give each question a "
                "short 'header' when asking more than one — it names the tab. Use for high-impact "
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
            # No user-facing surface at all (print/JSON mode), and that will not
            # change later in the run — take the tool away rather than let the
            # model retry it every turn.
            disabled = _disable_tool(runtime, self.name)
            return ToolResult.error(
                invocation.id,
                "ask_user needs an interactive session and is unavailable in this mode."
                + (
                    " The tool has been disabled for the rest of this session — do not "
                    "try it again; ask the question in plain text instead."
                    if disabled
                    else ""
                ),
            )

        questions = params.questions
        if getattr(ui, "supports_components", True):
            responses = await self._ask(ui, questions, params.timeout)
        else:
            # An RPC client can answer dialogs but cannot render the component,
            # so ask through the protocol's fixed shapes instead.
            responses = await ask_over_bridge(
                ui,
                questions,
                [normalize_options(q.options) for q in questions],
                params.timeout,
            )

        if responses is None:
            # Cancelling discards the whole questionnaire — with a review step
            # there is no such thing as a half-submitted set of answers.
            return ToolResult.ok(
                invocation.id,
                self._format_answers([]),
                metadata={"cancelled": True, "answers": []},
            )

        answers: list[dict] = []
        for question, response in zip(questions, responses, strict=True):
            if response is None:
                continue
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

    async def _ask(
        self,
        ui: Any,
        questions: list[Any],
        timeout: int | None,
    ) -> list[dict | None] | None:
        """Run the whole questionnaire in one dialog.

        Returns one response per question, or ``None`` if the user cancelled.
        A single question is shown bare; several get the tab bar and a review
        step, so earlier answers can still be changed before submitting.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[dict | None] | None] = loop.create_future()
        timeout_task_ref: list[asyncio.Task | None] = [None]
        close_ref: list[Any] = [None]

        def _settle(value: list[dict | None] | None) -> None:
            task = timeout_task_ref[0]
            if task is not None and not task.done():
                task.cancel()
            if not fut.done():
                fut.set_result(value)

        def _factory(_tui, _theme, _kb, done):
            close_ref[0] = done
            children = [
                _AskUserComponent(
                    question=q.question,
                    context=q.context,
                    options=normalize_options(q.options),
                    allow_multiple=q.allow_multiple,
                    allow_freeform=q.allow_freeform,
                    multiline=q.multiline,
                    on_done=lambda _v: None,  # replaced below
                    theme=_theme,
                )
                for q in questions
            ]

            def _finish(value: Any) -> None:
                if value is None:
                    _settle(None)
                elif value.get("kind") == "sequence":
                    _settle(value["answers"])
                else:
                    _settle([value])
                done(value)

            if len(children) == 1:
                children[0]._on_done = _finish
                component: Any = children[0]
            else:
                component = _AskUserSequence(
                    headers=[_header_for(q, i) for i, q in enumerate(questions)],
                    children=children,
                    on_done=_finish,
                    theme=_theme,
                    on_activity=_restart_timeout,
                )

            if timeout:
                _restart_timeout()
            return component

        def _restart_timeout() -> None:
            """(Re)arm the inactivity timer — every keystroke pushes it back."""
            if not timeout:
                return
            task = timeout_task_ref[0]
            if task is not None and not task.done():
                task.cancel()

            async def _auto_dismiss() -> None:
                await asyncio.sleep(timeout / 1000)
                _settle(None)
                # Tear the dialog down too, or it stays on screen owning input
                # after the tool call has already returned.
                if close_ref[0] is not None:
                    close_ref[0](None)

            timeout_task_ref[0] = asyncio.ensure_future(_auto_dismiss())

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
