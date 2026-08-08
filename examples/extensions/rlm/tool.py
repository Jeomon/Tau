"""A tool that answers questions about text too large to read.

Reading a 40 MB log or a thousand-file dump the ordinary way costs the whole
conversation: once it is in the transcript it is resent on every later turn,
and the model gets worse as its context fills with material it has finished
with. Recursive Language Models (arXiv:2512.24601) invert that — the text stays
in a variable, the model writes Python to narrow it down, and only its findings
enter the conversation.

So the tool returns an *answer*, not the text. What lands in the transcript is
a few hundred characters regardless of whether the input was ten kilobytes or
ten megabytes.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

from .repl import FinalAnswer, ReplEnvironment

#: Root turns before the tool gives up and asks for an answer from what it has.
DEFAULT_MAX_ITERATIONS = 8

#: Recursive model calls allowed per run. The cost of a run is dominated by
#: these, so it is the number worth bounding.
DEFAULT_SUB_CALL_BUDGET = 8

#: Engine budget for one run, overriding the much shorter engine-wide default.
#: A run is a chain of root turns each of which may fan out into sub-calls, so
#: the wall clock is dominated by model latency rather than local work.
RLM_TIMEOUT_SECONDS = 900.0

#: Refuse rather than silently truncate below this. A context that fits in the
#: conversation should just be read; the tool earns its cost above it.
MIN_WORTHWHILE_CHARS = 2000

#: Characters of a code line or error echoed into a progress line. Progress is
#: glanced at, not read, and a wrapped line makes the log harder to follow.
PROGRESS_SNIPPET = 64

_CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)

_SYSTEM_PROMPT = """\
You are answering a question about a large body of text you cannot see.

The text is already loaded in a Python REPL as the variable `context` (a str).
`context_length` holds its length. You interact with it only by writing Python.

Reply with exactly one ```python code block per turn. It is executed and you
are shown what it printed, truncated. Print deliberately: printing the whole
context wastes the turn and tells you nothing.

Available: re, json, math, statistics, collections, itertools, textwrap, and
ordinary builtins. There is no file or network access.

You also have:
  llm_query(prompt) -> str
      Ask another model about a slice you have selected. Pass the text you want
      considered inside the prompt. Use it for judgement a regex cannot make,
      such as classifying or summarising a chunk. It costs a model call, so
      narrow the text down first.

Finish with either:
  FINAL("your answer")     - answer directly
  FINAL_VAR("variable")    - answer with a variable's contents, for long answers
                             you assembled programmatically

Strategy that works: look at the shape first (length, a small slice, how it is
delimited), narrow with string or regex operations, then use llm_query only on
what is left. Answer as soon as you are confident."""


class RLMQueryParams(BaseModel):
    """Validated parameters for the RLM query tool."""

    query: str = Field(description="The question to answer about the text.")
    paths: list[str] | None = Field(
        default=None,
        description=(
            "Files to load as the context. Globs are expanded. Combine with an "
            "explicit question rather than asking for a summary of everything."
        ),
    )
    text: str | None = Field(
        default=None,
        description="Literal text to use as the context instead of reading files.",
    )
    max_iterations: int = Field(
        default=DEFAULT_MAX_ITERATIONS,
        ge=1,
        le=30,
        description="Maximum root turns spent exploring before answering.",
    )


def _load_paths(paths: list[str], base: Path) -> tuple[str, list[str], list[str]]:
    """Read every path into one context string.

    Files are labelled with their name in the joined text, because a model
    slicing the context needs to know where one file ends and the next starts;
    an unlabelled concatenation makes every offset meaningless.
    """
    loaded: list[str] = []
    failed: list[str] = []
    parts: list[str] = []
    for raw in paths:
        candidate = Path(raw)
        matches = (
            [candidate]
            if candidate.is_absolute() or not any(ch in raw for ch in "*?[")
            else sorted(base.glob(raw))
        )
        if not candidate.is_absolute() and not any(ch in raw for ch in "*?["):
            matches = [base / raw]
        for match in matches:
            try:
                body = match.read_text(encoding="utf-8", errors="replace")
            except OSError:
                failed.append(str(match))
                continue
            loaded.append(str(match))
            parts.append(f"===== {match} =====\n{body}")
    return "\n\n".join(parts), loaded, failed


def _snippet(text: str) -> str:
    """The first line worth showing from ``text``, shortened to one row.

    Comments and imports are skipped rather than taken: a cell that opens with
    ``import re`` would otherwise be summarised by its least informative line,
    which tells the reader nothing about what the turn is doing.
    """
    fallback = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not fallback:
            fallback = stripped
        if stripped.startswith(("#", "import ", "from ")):
            continue
        return _shorten(stripped)
    return _shorten(fallback)


def _shorten(line: str) -> str:
    if len(line) > PROGRESS_SNIPPET:
        return line[: PROGRESS_SNIPPET - 1] + "…"
    return line


def _duration(seconds: float) -> str:
    """Elapsed time at the precision a reader can act on."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"


def _outcome(cell: Any, sub_calls: int = 0) -> str:
    """How a finished cell went, in a few words.

    Time comes last and is always present: when a run feels stuck, the first
    question is how long the last thing took, and a line without it cannot
    answer that. Sub-calls sit next to it because they are what made it slow.
    """
    parts: list[str] = []
    if cell.stderr:
        parts.append(f"error: {_snippet(cell.stderr)}")
    elif not cell.stdout:
        parts.append("no output")
    else:
        lines = cell.stdout.count("\n") + 1
        parts.append(f"{len(cell.stdout)} chars, {lines} line{'s' if lines != 1 else ''}")
    if sub_calls:
        parts.append(f"{sub_calls} sub-call{'s' if sub_calls != 1 else ''}")
    parts.append(_duration(cell.elapsed))
    return ", ".join(parts)


async def _complete(llm: Any, system_prompt: str, conversation: str) -> str:
    """One model call returning its text."""
    from tau.inference.types import LLMContext, TextDeltaEvent, TextEndEvent
    from tau.message.types import UserMessage

    events = await llm.invoke(
        LLMContext(
            messages=[UserMessage.from_text(conversation)],
            system_prompt=system_prompt,
        )
    )
    text_end = next((e for e in events if isinstance(e, TextEndEvent)), None)
    if text_end is not None:
        return str(text_end.text.content)
    return "".join(e.text.content for e in events if isinstance(e, TextDeltaEvent))


class RLMTool(Tool):
    """Answer a question about text too large to put in the conversation."""

    def __init__(
        self,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        sub_call_budget: int = DEFAULT_SUB_CALL_BUDGET,
    ) -> None:
        super().__init__(
            name="rlm",
            description=(
                "Answer a question about text far too large to read into the "
                "conversation - a huge log, a whole directory, a giant JSON "
                "dump. The text is loaded into a Python REPL that a model "
                "explores programmatically and sub-queries in pieces; only the "
                "answer comes back, so the transcript stays small no matter how "
                "big the input. Prefer 'read' for anything you could simply read."
            ),
            schema=RLMQueryParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            # A run is a chain of model turns plus recursive sub-calls, so it
            # routinely outlives the engine default: the 120s ceiling was
            # cancelling real runs and discarding all the work they had done.
            timeout_seconds=RLM_TIMEOUT_SECONDS,
            prompt_guidelines=(
                "rlm: use for questions over inputs too large to read "
                "directly. Ask a specific question - it explores to answer that "
                "question, and a vague one wastes model calls."
            ),
        )
        self._max_iterations = max_iterations
        self._sub_call_budget = sub_call_budget

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = RLMQueryParams.model_validate(invocation.params)
        base = context.cwd if context is not None and context.cwd is not None else Path.cwd()
        llm = getattr(context, "llm", None)
        if llm is None:
            return ToolResult.error(
                invocation.id, "rlm needs an active model and none was available."
            )

        failed: list[str]
        if params.text is not None:
            body, loaded, failed = params.text, ["<inline text>"], []
        elif params.paths:
            body, loaded, failed = _load_paths(params.paths, base)
        else:
            return ToolResult.error(invocation.id, "rlm needs either paths or text.")

        if not body:
            detail = f" Could not read: {', '.join(failed)}" if failed else ""
            return ToolResult.error(invocation.id, f"No context was loaded.{detail}")
        if len(body) < MIN_WORTHWHILE_CHARS:
            return ToolResult.error(
                invocation.id,
                f"The context is only {len(body)} characters. Read it directly "
                "with 'read' instead — this tool costs several model calls and "
                "earns that back only on input too large to read.",
            )

        loop = asyncio.get_running_loop()

        #: The current turn's progress line, which a sub-call decorates while
        #: it is in flight. Written by the turn loop, read from the worker
        #: thread running the cell.
        head_line = ""

        def _sub_query(prompt: str) -> str:
            """Bridge the REPL's synchronous world back to the async model.

            The cell runs in a worker thread so the event loop stays free; the
            sub-call has to hop back onto the loop to reach the model.
            """
            # A cell that calls this is the longest a run ever goes without
            # saying anything: the turn line was written before the cell
            # started and nothing else is emitted until it returns. Announce
            # the sub-call from here — fire-and-forget, because blocking this
            # thread on a progress update would serialise it behind the very
            # loop the sub-call needs.
            if tool_execution_update_callback is not None:
                asyncio.run_coroutine_threadsafe(
                    _progress(f"{head_line} ← sub-call {env.sub_calls} running…", replace=True),
                    loop,
                )
            future = asyncio.run_coroutine_threadsafe(
                _complete(llm, "Answer the question about the provided text.", prompt), loop
            )
            try:
                return future.result(timeout=120)
            except Exception as error:  # a failed sub-call is data, not a crash
                return f"[sub-query failed: {type(error).__name__}: {error}]"

        env = ReplEnvironment(
            context=body, sub_query=_sub_query, sub_call_budget=self._sub_call_budget
        )
        transcript = [f"Question: {params.query}", f"context_length = {len(body)}"]
        answer: str | None = None
        iterations = 0
        budget = min(params.max_iterations, self._max_iterations)

        progress: list[str] = [
            f"loaded {len(body)} chars from {len(loaded)} source{'s' if len(loaded) != 1 else ''}"
        ]

        async def _progress(line: str, *, replace: bool = False) -> None:
            """Report what the run is doing while it does it.

            A run is several model calls long and produces nothing until the
            last one returns, so without this the caller watches a spinner for
            a minute with no way to tell exploring from stuck. ``replace``
            rewrites the current turn's line as it advances, so the log stays
            one line per turn rather than three.
            """
            if tool_execution_update_callback is None:
                return
            if replace and progress:
                progress[-1] = line
            else:
                progress.append(line)
            await tool_execution_update_callback(
                ToolResult.ok(
                    invocation.id,
                    "\n".join(progress),
                    metadata={
                        "running": True,
                        "context_chars": len(body),
                        "sources": loaded,
                        "iterations": iterations,
                        "sub_calls": env.sub_calls,
                    },
                )
            )

        for turn in range(1, budget + 1):
            iterations = turn
            if signal is not None and signal.is_set():
                return ToolResult.error(invocation.id, "rlm cancelled.")

            head = f"turn {turn}/{budget}"
            head_line = f"{head} · deciding what to run"
            await _progress(head_line)
            reply = await _complete(llm, _SYSTEM_PROMPT, "\n\n".join(transcript))
            blocks = _CODE_BLOCK.findall(reply)
            if not blocks:
                # No code and no FINAL means the model answered in prose. Take
                # it rather than burning another turn insisting on ceremony.
                answer = reply.strip()
                head_line = f"{head} · answered directly"
                await _progress(head_line, replace=True)
                break

            code = blocks[0]
            transcript.append(f"You ran:\n```python\n{code}```")
            head_line = f"{head} · {_snippet(code)}"
            await _progress(head_line, replace=True)
            before, started = env.sub_calls, time.monotonic()
            try:
                cell = await asyncio.to_thread(env.run, code)
            except FinalAnswer as final:
                answer = final.answer
                elapsed = _duration(time.monotonic() - started)
                await _progress(f"{head_line} → answer, {elapsed}", replace=True)
                break
            await _progress(f"{head_line} → {_outcome(cell, env.sub_calls - before)}", replace=True)
            transcript.append(f"Output:\n{cell.for_model()}")

        if answer is None:
            # Out of turns. Ask for the best answer from what was gathered
            # rather than returning nothing for the work already paid for.
            await _progress("out of turns · answering from what was found")
            transcript.append("You are out of turns. Answer the question now from what you found.")
            answer = (await _complete(llm, _SYSTEM_PROMPT, "\n\n".join(transcript))).strip()

        return ToolResult.ok(
            invocation.id,
            answer or "(no answer produced)",
            metadata={
                "context_chars": len(body),
                "sources": loaded,
                "unreadable": failed,
                "iterations": iterations,
                "sub_calls": env.sub_calls,
            },
        )
