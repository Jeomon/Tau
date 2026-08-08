"""Recursive Language Model querying over text too large to read.

The idea (arXiv:2512.24601): don't feed a huge input to the model, hand it a
variable holding that input plus a Python prompt. It narrows the text down
itself and sub-queries a model on the pieces that matter, so the conversation
grows with the *answer* rather than with the size of the input.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from tau.tool.types import ToolContext, ToolInvocation
from tests.ext_loader import load_extension

_rlm = load_extension("rlm")
_repl = importlib.import_module(f"{_rlm.__name__}.repl")
_tool = importlib.import_module(f"{_rlm.__name__}.tool")

MAX_CELL_OUTPUT = _repl.MAX_CELL_OUTPUT
FinalAnswer = _repl.FinalAnswer
ReplEnvironment = _repl.ReplEnvironment
MIN_WORTHWHILE_CHARS = _tool.MIN_WORTHWHILE_CHARS
RLMTool = _tool.RLMTool
register = _rlm.register


def _env(context: str = "alpha\nbeta\ngamma\nbeta\n", **kwargs) -> ReplEnvironment:
    kwargs.setdefault("sub_query", lambda prompt: f"<sub:{prompt}>")
    return ReplEnvironment(context=context, **kwargs)


class _ScriptedLLM:
    """Replays canned replies, recording root and sub-call counts separately."""

    def __init__(self, replies: list[str], sub_reply: str = "a sub-answer") -> None:
        self._replies = iter(replies)
        self._sub_reply = sub_reply
        self.root_calls = 0
        self.sub_calls = 0

    async def invoke(self, context):
        from tau.inference.types import TextEndEvent
        from tau.message.types import TextContent

        if "Answer the question about" in (context.system_prompt or ""):
            self.sub_calls += 1
            return [TextEndEvent(text=TextContent(content=self._sub_reply))]
        self.root_calls += 1
        return [TextEndEvent(text=TextContent(content=next(self._replies)))]


def _run(tool: RLMTool, cwd: Path, llm, **params):
    invocation = ToolInvocation(id="call-1", name=tool.name, cwd=cwd, params=params)
    return asyncio.run(tool.execute(invocation, context=ToolContext(cwd=cwd, llm=llm)))


class TestReplNamespace:
    def test_the_context_is_a_variable_not_a_prompt(self):
        """The whole premise: the model reaches the text through code."""
        env = _env()

        assert env.run("print(context_length)").for_model().strip() == str(len(env.context))

    def test_text_operations_are_available(self):
        env = _env()

        output = env.run("print(len(re.findall(r'beta', context)))").for_model()

        assert output.strip() == "2"

    def test_file_access_is_not_reachable(self):
        """Not a security boundary — the terminal tool already runs anything —
        but an unlisted name should fail loudly rather than do something."""
        env = _env()

        assert "NameError" in env.run("open('/etc/passwd')").for_model()

    def test_an_error_comes_back_as_output_rather_than_raising(self):
        """A traceback is information the model can act on. Aborting the run
        would throw away everything gathered so far."""
        env = _env()

        assert "NameError" in env.run("print(nope)").for_model()

    def test_a_cell_that_prints_everything_is_truncated(self):
        """Printing the whole context would defeat the point of the exercise."""
        env = _env(context="x" * (MAX_CELL_OUTPUT * 3))

        output = env.run("print(context)").for_model()

        assert "truncated" in output
        assert len(output) < MAX_CELL_OUTPUT * 2

    def test_state_persists_between_cells(self):
        env = _env()
        env.run("found = re.findall(r'beta', context)")

        assert env.run("print(len(found))").for_model().strip() == "2"


class TestRecursion:
    def test_a_sub_query_reaches_a_model(self):
        env = _env()

        assert "<sub:classify this>" in env.run("print(llm_query('classify this'))").for_model()

    def test_sub_calls_are_capped(self):
        """Cost is dominated by these, so the budget is what bounds a run."""
        env = _env(sub_call_budget=2)

        for _ in range(4):
            env.run("print(llm_query('again'))")

        assert env.sub_calls == 2

    def test_an_exhausted_budget_tells_the_model_to_answer(self):
        env = _env(sub_call_budget=0)

        assert "budget" in env.run("print(llm_query('x'))").for_model()

    def test_an_empty_prompt_is_refused_without_spending_a_call(self):
        env = _env()

        env.run("print(llm_query('   '))")

        assert env.sub_calls == 0


class TestFinishing:
    def test_final_ends_the_run(self):
        env = _env()

        with pytest.raises(FinalAnswer) as caught:
            env.run("FINAL('the answer')")

        assert caught.value.answer == "the answer"

    def test_final_var_answers_from_a_variable(self):
        """An answer assembled in code can be far longer than one retyped, and
        retyping is where a long answer gets silently truncated."""
        env = _env()
        env.run("built = 'line\\n' * 500")

        with pytest.raises(FinalAnswer) as caught:
            env.run("FINAL_VAR('built')")

        assert len(caught.value.answer) == 2500

    def test_final_var_on_an_unknown_name_is_reported(self):
        env = _env()

        assert "NameError" in env.run("FINAL_VAR('nope')").for_model()


class TestTool:
    def test_it_answers_without_the_text_entering_the_transcript(self, tmp_path):
        """The point of the tool: a huge input, a small result."""
        log = tmp_path / "big.log"
        lines = [f"INFO worker {i} ok" for i in range(4000)]
        lines[1234] = "ERROR disk quota exceeded on /dev/sda1"
        log.write_text("\n".join(lines))
        llm = _ScriptedLLM(
            [
                "```python\nerrs = re.findall(r'ERROR.*', context)\nprint(errs)\n```",
                "```python\nFINAL('one disk-quota error on /dev/sda1')\n```",
            ]
        )

        result = _run(
            RLMTool(), tmp_path, llm, query="What errors occurred?", paths=["big.log"]
        )

        assert not result.is_error
        assert result.content == "one disk-quota error on /dev/sda1"
        assert len(result.content) < log.stat().st_size / 1000
        assert result.metadata["iterations"] == 2

    def test_a_small_input_is_refused_with_a_cheaper_suggestion(self, tmp_path):
        """Several model calls to read something you could just read is a
        worse answer, not a better one."""
        small = tmp_path / "small.txt"
        small.write_text("hello")

        result = _run(RLMTool(), tmp_path, _ScriptedLLM([]), query="q", paths=["small.txt"])

        assert result.is_error
        assert "read" in result.content

    def test_literal_text_can_be_the_context(self, tmp_path):
        llm = _ScriptedLLM(["```python\nFINAL('counted')\n```"])

        result = _run(
            RLMTool(), tmp_path, llm, query="how many?", text="word " * MIN_WORTHWHILE_CHARS
        )

        assert not result.is_error
        assert result.content == "counted"

    def test_sub_calls_from_inside_a_cell_reach_the_model(self, tmp_path):
        """The recursion has to cross from the worker thread running the cell
        back onto the event loop, which is the part that can silently deadlock."""
        llm = _ScriptedLLM(
            [
                "```python\nverdict = llm_query('classify')\nprint(verdict)\n```",
                "```python\nFINAL(verdict)\n```",
            ],
            sub_reply="it is a disk fault",
        )

        result = _run(
            RLMTool(), tmp_path, llm, query="what is it?", text="x" * MIN_WORTHWHILE_CHARS
        )

        assert llm.sub_calls == 1
        assert result.content == "it is a disk fault"

    def test_running_out_of_turns_still_answers(self, tmp_path):
        """The exploration was paid for; returning nothing wastes it."""
        llm = _ScriptedLLM(
            ["```python\nprint(context_length)\n```"] * 2 + ["what I found: it is large"]
        )

        result = _run(
            RLMTool(),
            tmp_path,
            llm,
            query="how big?",
            text="x" * MIN_WORTHWHILE_CHARS,
            max_iterations=2,
        )

        assert not result.is_error
        assert result.content == "what I found: it is large"

    def test_a_prose_reply_is_taken_as_the_answer(self, tmp_path):
        """Insisting on ceremony would cost another turn for no information."""
        llm = _ScriptedLLM(["There are three errors."])

        result = _run(
            RLMTool(), tmp_path, llm, query="how many?", text="x" * MIN_WORTHWHILE_CHARS
        )

        assert result.content == "There are three errors."

    def test_without_a_model_it_fails_rather_than_pretending(self, tmp_path):
        invocation = ToolInvocation(
            id="c", name="rlm", cwd=tmp_path, params={"query": "q", "text": "x" * 5000}
        )

        result = asyncio.run(RLMTool().execute(invocation, context=ToolContext(cwd=tmp_path)))

        assert result.is_error

    def test_neither_paths_nor_text_is_an_error(self, tmp_path):
        result = _run(RLMTool(), tmp_path, _ScriptedLLM([]), query="q")

        assert result.is_error
        assert "paths or text" in result.content

    def test_unreadable_paths_are_reported_not_swallowed(self, tmp_path):
        result = _run(RLMTool(), tmp_path, _ScriptedLLM([]), query="q", paths=["missing.log"])

        assert result.is_error

    def test_several_files_are_labelled_so_offsets_mean_something(self, tmp_path):
        """An unlabelled concatenation makes every offset in the context
        meaningless to a model slicing it."""
        (tmp_path / "a.txt").write_text("aaa " * 600)
        (tmp_path / "b.txt").write_text("bbb " * 600)
        llm = _ScriptedLLM(
            ["```python\nFINAL(str([n for n in ('a.txt', 'b.txt') if n in context]))\n```"]
        )

        result = _run(RLMTool(), tmp_path, llm, query="q", paths=["a.txt", "b.txt"])

        assert result.content == "['a.txt', 'b.txt']"
        assert len(result.metadata["sources"]) == 2


class TestRegistration:
    def test_the_tool_is_registered_by_default(self):
        registered: list = []
        register(type("API", (), {"config": {}, "register_tool": registered.append})())

        assert [tool.name for tool in registered] == ["rlm"]

    def test_it_can_be_turned_off(self):
        registered: list = []
        register(
            type("API", (), {"config": {"enabled": False}, "register_tool": registered.append})()
        )

        assert registered == []

    def test_budgets_come_from_configuration(self):
        registered: list = []
        register(
            type(
                "API",
                (),
                {
                    "config": {"max_iterations": 3, "sub_call_budget": 2},
                    "register_tool": registered.append,
                },
            )()
        )

        assert registered[0]._max_iterations == 3
        assert registered[0]._sub_call_budget == 2

    def test_the_manifest_matches_the_settings_the_code_reads(self):
        """A manifest field the code ignores is a setting that silently does
        nothing."""
        import json

        from tests.ext_loader import extension_dir

        manifest = json.loads((extension_dir("rlm") / "manifest.json").read_text())
        keys = {field["key"] for field in manifest["tau"]["settings"]["fields"]}

        assert keys == {"enabled", "max_iterations", "sub_call_budget"}
