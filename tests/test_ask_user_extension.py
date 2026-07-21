from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.extensions.loader import _RuntimeRef
from tau.tool.types import ToolInvocation

# Add the bundled ask_user extension directory to sys.path to allow imports under test.
sys.path.insert(
    0,
    str(Path(__file__).parent.parent / ".tau" / "extensions" / "ask_user"),
)

from component import _AskUserComponent, _AskUserSequence  # noqa: E402
from schema import (  # noqa: E402
    AskUserOption,
    AskUserParams,
    AskUserQuestion,
    QuestionValidationError,
    validate_questions,
)
from tool import AskUserTool, _header_for  # noqa: E402

from tau.tui.buffer import Buffer  # noqa: E402
from tau.tui.geometry import Rect  # noqa: E402
from tau.tui.input import KeyEvent  # noqa: E402


def test_ask_user_tool_initialization() -> None:
    runtime_ref = _RuntimeRef()
    tool = AskUserTool(runtime_ref)
    assert tool.name == "ask_user"
    assert tool.schema == AskUserParams


def test_ask_user_tool_requires_tui() -> None:
    async def exercise() -> None:
        runtime = SimpleNamespace(
            session_manager=None,
            agent=None,
            settings_manager=None,
            _layout=None,
        )
        runtime_ref = _RuntimeRef()
        runtime_ref.runtime = runtime

        tool = AskUserTool(runtime_ref)
        invocation = ToolInvocation(
            id="call_1",
            name="ask_user",
            cwd=Path.cwd(),
            params={"questions": [{"question": "Should we proceed?"}]},
        )
        result = await tool.execute(invocation)
        assert result.is_error
        assert "requires an interactive TUI session" in result.content

    asyncio.run(exercise())


# ── Helpers ──────────────────────────────────────────────────────────────────


def _key(name: str, char: str | None = None, shift: bool = False) -> KeyEvent:
    return KeyEvent(key=name, char=char, shift=shift)


def _component(**kwargs):
    """A single-question component; every field has a sane default."""
    results: list = []
    defaults = dict(
        question="Pick one",
        context=None,
        options=[AskUserOption(title="Alpha"), AskUserOption(title="Beta")],
        allow_multiple=False,
        allow_freeform=True,
        multiline=False,
        on_done=results.append,
    )
    defaults.update(kwargs)
    return _AskUserComponent(**defaults), results


def _rendered_text(buf) -> str:
    """Flatten a rendered Buffer back into plain text for assertions."""
    return "\n".join(
        "".join(buf.get(x, y).symbol for x in range(buf.area.width))
        for y in range(buf.area.height)
    )


def _type(component, text: str) -> None:
    for ch in text:
        component.handle_input(_key(ch, char=ch))


# ── Multi-select + free text (both, not either/or) ───────────────────────────


class TestMultiSelectFreeText:
    def test_checked_options_and_typed_text_come_back_together(self):
        c, results = _component(allow_multiple=True)

        c.handle_input(_key("space"))  # tick Alpha
        c.handle_input(_key("down"))
        c.handle_input(_key("down"))  # onto "Type something…"
        c.handle_input(_key("space"))  # open the editor
        assert c.is_editing
        _type(c, "and a custom note")
        c.handle_input(_key("enter"))  # save, back to the list
        assert not c.is_editing
        assert not results  # saving text must not submit the question

        c.handle_input(_key("enter"))  # now submit

        assert results == [
            {
                "kind": "selection",
                "selections": ["Alpha", "and a custom note"],
                "text": "and a custom note",
            }
        ]

    def test_text_alone_is_a_valid_multi_select_answer(self):
        c, results = _component(allow_multiple=True)

        c.handle_input(_key("down"))
        c.handle_input(_key("down"))
        c.handle_input(_key("space"))
        _type(c, "neither")
        c.handle_input(_key("enter"))
        c.handle_input(_key("enter"))

        assert results[0]["selections"] == ["neither"]

    def test_emptying_the_editor_clears_the_saved_text(self):
        c, results = _component(allow_multiple=True)

        c.handle_input(_key("down"))
        c.handle_input(_key("down"))
        c.handle_input(_key("space"))
        _type(c, "oops")
        c.handle_input(_key("enter"))
        c.handle_input(_key("space"))  # reopen
        for _ in range(4):
            c.handle_input(_key("backspace"))
        c.handle_input(_key("enter"))  # save empty → cleared

        c.handle_input(_key("up"))  # back onto Beta
        c.handle_input(_key("space"))
        c.handle_input(_key("enter"))

        assert results[0] == {"kind": "selection", "selections": ["Beta"]}
        assert "text" not in results[0]

    def test_single_select_editor_still_submits_immediately(self):
        c, results = _component(allow_multiple=False)

        c.handle_input(_key("down"))
        c.handle_input(_key("down"))
        c.handle_input(_key("enter"))  # open editor
        _type(c, "freeform")
        c.handle_input(_key("enter"))

        assert results == [{"kind": "freeform", "text": "freeform"}]

    def test_saved_text_is_visible_on_the_row(self):
        c, _ = _component(allow_multiple=True)
        c.handle_input(_key("down"))
        c.handle_input(_key("down"))
        c.handle_input(_key("space"))
        _type(c, "custom answer")
        c.handle_input(_key("enter"))

        buf = Buffer.empty(Rect(0, 0, 80, 40))
        c.render_cells(Rect(0, 0, 80, 40), buf)
        rendered = _rendered_text(buf)
        assert "custom answer" in rendered


# ── Tabbed multi-question flow ───────────────────────────────────────────────


def _sequence(count: int = 2, **kwargs):
    results: list = []
    children = []
    for i in range(count):
        child, _ = _component(
            question=f"Question {i}",
            options=[AskUserOption(title=f"Opt{i}A"), AskUserOption(title=f"Opt{i}B")],
            **kwargs,
        )
        children.append(child)
    seq = _AskUserSequence(
        headers=[f"H{i}" for i in range(count)],
        children=children,
        on_done=results.append,
        on_activity=None,
    )
    return seq, children, results


class TestSequence:
    def test_answering_advances_without_submitting(self):
        seq, _, results = _sequence()

        seq.handle_input(_key("enter"))  # answer Q0

        assert results == []  # nothing submitted yet
        assert seq._index == 1  # moved to Q1

    def test_all_answered_lands_on_review_then_submits(self):
        seq, _, results = _sequence()

        seq.handle_input(_key("enter"))
        seq.handle_input(_key("enter"))

        assert seq._on_review
        seq.handle_input(_key("enter"))

        assert len(results) == 1
        assert results[0]["kind"] == "sequence"
        assert [a["selections"] for a in results[0]["answers"]] == [["Opt0A"], ["Opt1A"]]

    def test_an_earlier_answer_can_be_revised(self):
        seq, _, results = _sequence()

        seq.handle_input(_key("enter"))  # Q0 = Opt0A
        seq.handle_input(_key("enter"))  # Q1 = Opt1A  → review
        seq.handle_input(_key("left"))  # back to Q1
        seq.handle_input(_key("left"))  # back to Q0
        assert seq._index == 0
        seq.handle_input(_key("down"))  # move to Opt0B
        seq.handle_input(_key("enter"))  # re-answer

        seq._index = len(seq._children)  # to review
        seq.handle_input(_key("enter"))

        assert results[0]["answers"][0]["selections"] == ["Opt0B"]

    def test_review_refuses_to_submit_while_something_is_unanswered(self):
        seq, _, results = _sequence()

        seq._index = len(seq._children)  # jump to review with nothing answered
        seq.handle_input(_key("enter"))

        assert results == []
        assert "Still unanswered" in seq._warning

    def test_escape_cancels_the_whole_dialog(self):
        seq, _, results = _sequence()

        seq.handle_input(_key("enter"))  # answer one
        seq.handle_input(_key("escape"))

        assert results == [None]

    def test_tabs_do_not_steal_arrows_from_the_editor(self):
        seq, children, _ = _sequence()

        seq.handle_input(_key("down"))
        seq.handle_input(_key("down"))
        seq.handle_input(_key("enter"))  # open Q0's freeform editor
        assert children[0].is_editing

        seq.handle_input(_key("left"))  # must move the text cursor, not the tab

        assert seq._index == 0

    def test_tab_bar_marks_answered_questions(self):
        seq, _, _ = _sequence()
        seq.handle_input(_key("enter"))

        buf = Buffer.empty(Rect(0, 0, 80, 40))
        seq.render_cells(Rect(0, 0, 80, 40), buf)
        rendered = _rendered_text(buf)
        assert "H0" in rendered and "H1" in rendered and "Review" in rendered
        assert "✔" in rendered


# ── Headers ──────────────────────────────────────────────────────────────────


class TestHeaders:
    def test_over_long_header_is_rejected(self):
        with pytest.raises(QuestionValidationError, match="tab label"):
            validate_questions(
                [AskUserQuestion(question="Q?", header="a much too long header value")]
            )

    def test_header_falls_back_to_the_question_text(self):
        assert _header_for(AskUserQuestion(question="Short?"), 0) == "Short?"
        long = _header_for(AskUserQuestion(question="A very long question indeed?"), 0)
        assert len(long) <= 14
        assert long.endswith("…")
        assert _header_for(AskUserQuestion(question="Q?", header="Auth"), 0) == "Auth"


# ── Headless self-disable ────────────────────────────────────────────────────


class TestHeadlessDisable:
    def _run(self, engine):
        async def exercise():
            runtime = SimpleNamespace(
                session_manager=None,
                agent=SimpleNamespace(_engine=engine),
                extension_generation=0,
                settings_manager=None,
                _layout=None,
            )
            runtime_ref = _RuntimeRef()
            runtime_ref.runtime = runtime
            tool = AskUserTool(runtime_ref)
            invocation = ToolInvocation(
                id="call_1",
                name="ask_user",
                cwd=Path.cwd(),
                params={"questions": [{"question": "Should we proceed?"}]},
            )
            return await tool.execute(invocation), tool

        return asyncio.run(exercise())

    def test_tool_removes_itself_when_there_is_no_ui(self):
        other = SimpleNamespace(name="read")
        ask = SimpleNamespace(name="ask_user")
        engine = SimpleNamespace(
            tools=[other, ask], _tools={"read": other, "ask_user": ask}, llm=None
        )

        result, _ = self._run(engine)

        assert result.is_error
        assert [t.name for t in engine.tools] == ["read"]
        assert "ask_user" not in engine._tools
        assert "disabled for the rest of this session" in result.content

    def test_message_omits_the_disabled_note_when_nothing_was_removed(self):
        engine = SimpleNamespace(tools=[], _tools={}, llm=None)

        result, _ = self._run(engine)

        assert result.is_error
        assert "disabled for the rest of this session" not in result.content


# ── Tool ↔ component wiring ──────────────────────────────────────────────────


class _FakeUI:
    """Drives the component the tool builds, like a user at the keyboard."""

    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.component = None
        self.closed = False

    async def custom_inline(self, factory, kind="custom"):
        def _done(_value):
            self.closed = True

        self.component = factory(None, None, None, _done)
        for name in self.script:
            self.component.handle_input(KeyEvent(key=name, char=None))


def _run_tool(questions: list[dict], script: list[str]):
    async def exercise():
        ui = _FakeUI(script)
        runtime = SimpleNamespace(
            session_manager=None,
            agent=SimpleNamespace(_engine=SimpleNamespace(llm=None, tools=[], _tools={})),
            extension_generation=0,
            settings_manager=None,
            _layout=object(),  # non-None so ExtensionContext.ui is built
        )
        runtime_ref = _RuntimeRef()
        runtime_ref.runtime = runtime

        tool = AskUserTool(runtime_ref)
        invocation = ToolInvocation(
            id="c1", name="ask_user", cwd=Path.cwd(), params={"questions": questions}
        )
        import tau.extensions.context as ctx_mod

        original = ctx_mod.ExtensionContext.ui
        ctx_mod.ExtensionContext.ui = property(lambda self: ui)
        try:
            return await tool.execute(invocation), ui
        finally:
            ctx_mod.ExtensionContext.ui = original

    return asyncio.run(exercise())


class TestToolWiring:
    def test_one_question_skips_the_tab_bar_and_submits_on_enter(self):
        result, ui = _run_tool(
            [{"question": "Ship it?", "options": ["Yes", "No"]}],
            ["enter"],
        )

        assert isinstance(ui.component, _AskUserComponent)  # bare, not wrapped
        assert not result.is_error
        assert result.metadata["cancelled"] is False
        assert result.metadata["answers"][0]["response"] == "Yes"

    def test_several_questions_use_the_sequence_and_need_a_review_submit(self):
        result, ui = _run_tool(
            [
                {"question": "Ship it?", "header": "Ship", "options": ["Yes", "No"]},
                {"question": "Announce it?", "header": "Announce", "options": ["Yes", "No"]},
            ],
            ["enter", "enter", "enter"],  # answer, answer, submit from review
        )

        assert isinstance(ui.component, _AskUserSequence)
        assert [a["response"] for a in result.metadata["answers"]] == ["Yes", "Yes"]

    def test_cancelling_discards_every_answer(self):
        result, _ = _run_tool(
            [
                {"question": "Ship it?", "options": ["Yes", "No"]},
                {"question": "Announce it?", "options": ["Yes", "No"]},
            ],
            ["enter", "escape"],  # answer the first, then bail out
        )

        assert result.metadata["cancelled"] is True
        assert result.metadata["answers"] == []
        assert "cancelled" in result.content
