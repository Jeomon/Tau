from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tau.extensions.loader import _RuntimeRef
from tau.tool.types import ToolInvocation
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.input import KeyEvent
from tests.ext_loader import load_extension

# Loaded as a package, exactly as tau's loader does — its modules use relative
# imports and never occupy a bare name like `schema` or `tool`.
_PKG = load_extension("ask_user").__name__
_component = importlib.import_module(f"{_PKG}.component")
_schema = importlib.import_module(f"{_PKG}.schema")
_tool = importlib.import_module(f"{_PKG}.tool")

_AskUserComponent = _component._AskUserComponent
_AskUserSequence = _component._AskUserSequence
AskUserOption = _schema.AskUserOption
AskUserParams = _schema.AskUserParams
AskUserQuestion = _schema.AskUserQuestion
QuestionValidationError = _schema.QuestionValidationError
validate_questions = _schema.validate_questions
AskUserTool = _tool.AskUserTool
_header_for = _tool._header_for


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
        assert "needs an interactive session" in result.content

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


# ── RPC backend (no component — fixed dialog shapes) ─────────────────────────


class _FakeClient:
    """Answers every extension_ui_request the moment it is written.

    `_dialog` registers its future *before* writing, so resolving inline here
    is exactly what a client replying instantly looks like.
    """

    def __init__(self, answers: dict[str, Any], pending: dict) -> None:
        self.answers = answers
        self.pending = pending
        self.requests: list[dict] = []

    def write(self, obj: dict) -> None:
        if obj.get("type") != "extension_ui_request":
            return
        self.requests.append(obj)
        reply = self.answers.get(obj["method"], None)
        if callable(reply):
            reply = reply(obj)
        fut = self.pending.get(obj["id"])
        if fut is not None and not fut.done():
            fut.set_result(reply)


def _run_over_rpc(questions: list[dict], answers: dict[str, Any], monkeypatch):
    import tau.modes.rpc.mode as rpc_mode
    from tau.modes.rpc.mode import RpcExtensionUIContext

    pending: dict = {}
    bridge = RpcExtensionUIContext(pending)
    client = _FakeClient(answers, pending)
    monkeypatch.setattr(rpc_mode, "_write", client.write)

    async def exercise():
        ask = SimpleNamespace(name="ask_user")
        runtime = SimpleNamespace(
            session_manager=None,
            agent=SimpleNamespace(
                _engine=SimpleNamespace(tools=[ask], _tools={"ask_user": ask}, llm=None)
            ),
            extension_generation=0,
            settings_manager=None,
            _layout=None,
            extension_ui_bridge=bridge,
        )
        runtime_ref = _RuntimeRef()
        runtime_ref.runtime = runtime

        tool = AskUserTool(runtime_ref)
        invocation = ToolInvocation(
            id="c1", name="ask_user", cwd=Path.cwd(), params={"questions": questions}
        )
        return await tool.execute(invocation), runtime

    result, runtime = asyncio.run(exercise())
    return result, client, runtime


class TestRpcBackend:
    def test_single_select_uses_one_select_dialog(self, monkeypatch):
        result, client, runtime = _run_over_rpc(
            [{"question": "Ship it?", "options": ["Yes", "No"]}],
            {"select": "Yes"},
            monkeypatch,
        )

        assert [r["method"] for r in client.requests] == ["select"]
        assert client.requests[0]["options"] == ["Yes", "No", "Type something…"]
        assert result.metadata["answers"][0]["response"] == "Yes"
        # The tool stays available over RPC — it works here now.
        assert [t.name for t in runtime.agent._engine.tools] == ["ask_user"]

    def test_option_descriptions_ride_along_in_the_label(self, monkeypatch):
        _, client, _ = _run_over_rpc(
            [
                {
                    "question": "Auth?",
                    "options": [
                        {"title": "OAuth", "description": "no passwords"},
                        {"title": "Keys"},
                    ],
                    "allow_freeform": False,
                }
            ],
            {"select": "OAuth — no passwords"},
            monkeypatch,
        )

        assert client.requests[0]["options"][0] == "OAuth — no passwords"

    def test_multi_select_uses_the_multi_select_dialog(self, monkeypatch):
        result, client, _ = _run_over_rpc(
            [
                {
                    "question": "Which surfaces?",
                    "options": ["Web", "CLI", "Mobile"],
                    "allow_multiple": True,
                    "allow_freeform": False,
                }
            ],
            {"multi_select": ["Web", "Mobile"]},
            monkeypatch,
        )

        assert [r["method"] for r in client.requests] == ["multi_select"]
        assert result.metadata["answers"][0]["response"] == "Web, Mobile"

    def test_multi_select_none_chosen_is_a_real_answer(self, monkeypatch):
        result, _, _ = _run_over_rpc(
            [
                {
                    "question": "Which surfaces?",
                    "options": ["Web", "CLI"],
                    "allow_multiple": True,
                    "allow_freeform": False,
                }
            ],
            {"multi_select": []},
            monkeypatch,
        )

        assert result.metadata["cancelled"] is False
        assert result.metadata["answers"][0]["raw"]["selections"] == []

    def test_multi_select_plus_free_text_keeps_both(self, monkeypatch):
        result, client, _ = _run_over_rpc(
            [
                {
                    "question": "Which surfaces?",
                    "options": ["Web", "CLI"],
                    "allow_multiple": True,
                }
            ],
            {"multi_select": ["Web", "Type something…"], "input": "and audit logs"},
            monkeypatch,
        )

        assert [r["method"] for r in client.requests] == ["multi_select", "input"]
        raw = result.metadata["answers"][0]["raw"]
        assert raw["selections"] == ["Web", "and audit logs"]
        assert raw["text"] == "and audit logs"

    def test_freeform_choice_opens_a_text_dialog(self, monkeypatch):
        result, client, _ = _run_over_rpc(
            [{"question": "Ship it?", "options": ["Yes", "No"]}],
            {"select": "Type something…", "input": "next week"},
            monkeypatch,
        )

        assert [r["method"] for r in client.requests] == ["select", "input"]
        assert result.metadata["answers"][0]["response"] == "next week"

    def test_multiline_question_uses_the_editor_dialog(self, monkeypatch):
        result, client, _ = _run_over_rpc(
            [{"question": "Describe the bug", "multiline": True}],
            {"editor": "line one\nline two"},
            monkeypatch,
        )

        assert [r["method"] for r in client.requests] == ["editor"]
        assert result.metadata["answers"][0]["response"] == "line one\nline two"

    def test_context_is_shown_above_the_question(self, monkeypatch):
        _, client, _ = _run_over_rpc(
            [{"question": "Ship it?", "context": "CI is green.", "options": ["Yes", "No"]}],
            {"select": "Yes"},
            monkeypatch,
        )

        assert client.requests[0]["title"] == "CI is green.\n\nShip it?"

    def test_several_questions_ask_in_order(self, monkeypatch):
        result, client, _ = _run_over_rpc(
            [
                {"question": "Ship it?", "options": ["Yes", "No"], "allow_freeform": False},
                {"question": "Announce?", "options": ["Yes", "No"], "allow_freeform": False},
            ],
            {"select": "Yes"},
            monkeypatch,
        )

        assert len(client.requests) == 2
        assert [a["response"] for a in result.metadata["answers"]] == ["Yes", "Yes"]

    def test_dismissing_any_dialog_cancels_the_whole_set(self, monkeypatch):
        result, _, _ = _run_over_rpc(
            [
                {"question": "Ship it?", "options": ["Yes", "No"], "allow_freeform": False},
                {"question": "Announce?", "options": ["Yes", "No"], "allow_freeform": False},
            ],
            {"select": None},  # client dismissed
            monkeypatch,
        )

        assert result.metadata["cancelled"] is True
        assert result.metadata["answers"] == []


class TestTabStrip:
    """The strip is the shared Tabs widget, not a hand-joined ANSI string."""

    def _seq(self, headers, answered=()):
        children = [
            _component(question=f"Q{i}", options=[AskUserOption(title="A")])[0]
            for i in range(len(headers))
        ]
        seq = _AskUserSequence(headers=list(headers), children=children, on_done=lambda v: None)
        for i in answered:
            seq._answers[i] = {"kind": "selection", "selections": ["A"]}
        return seq

    def _cells(self, seq, width=70):
        buf = Buffer.empty(Rect(0, 0, width, 30))
        seq.render_cells(Rect(0, 0, width, 30), buf)
        return buf

    def test_titles_and_review_share_one_row(self):
        buf = self._cells(self._seq(["Auth", "Surfaces"]))
        row = "".join(buf.get(x, 0).symbol for x in range(70))

        assert "Auth" in row and "Surfaces" in row and "Review" in row
        assert "│" in row  # the widget's divider

    def test_an_answered_tab_keeps_its_tick_coloured(self):
        seq = self._seq(["Auth", "Surfaces"], answered=[0])
        buf = self._cells(seq)

        row = [buf.get(x, 0) for x in range(70)]
        tick = next(cell for cell in row if cell.symbol == "✔")
        label = next(cell for cell in row if cell.symbol == "A")

        # The span's own style wins over the tab style, so the tick is not
        # repainted by the selected/unselected colour applied to the label.
        assert tick.style.fg != label.style.fg

    def test_a_strip_wider_than_the_terminal_stays_one_row(self):
        seq = self._seq(["Authentication method", "Deployment surfaces", "Rollout strategy"])
        buf = self._cells(seq, width=40)

        # Row 1 is the blank separator, not a wrapped continuation of the tabs.
        assert "".join(buf.get(x, 1).symbol for x in range(40)).strip() == ""


class TestPreviewPane:
    """The preview frame is the shared Block widget."""

    def _rendered(self, options, width=88, cursor=0):
        c, _ = _component(options=options)
        for _ in range(cursor):
            c.handle_input(_key("down"))
        buf = Buffer.empty(Rect(0, 0, width, 30))
        rows = c.render_cells(Rect(0, 0, width, 30), buf)
        return [
            "".join(buf.get(x, y).symbol for x in range(width)).rstrip() for y in range(rows)
        ]

    def _opts(self):
        return [
            AskUserOption(title="Tabs", preview="mock line one\nmock line two"),
            AskUserOption(title="Plain"),
        ]

    def test_frame_and_title_are_drawn(self):
        out = "\n".join(self._rendered(self._opts()))

        assert "┌" in out and "┐" in out and "└" in out and "┘" in out
        assert "Preview" in out  # Block title on the top border

    def test_preview_content_sits_inside_the_frame(self):
        rows = self._rendered(self._opts())
        body = next(r for r in rows if "mock line one" in r)

        assert body.count("│") >= 2  # framed on both sides
        assert body.index("│") < body.index("mock line one")

    def test_an_option_without_a_preview_says_so(self):
        out = "\n".join(self._rendered(self._opts(), cursor=1))
        assert "(no preview for this option)" in out

    def test_overflowing_preview_is_truncated_not_grown(self):
        tall = AskUserOption(title="Tall", preview="\n".join(f"row {i}" for i in range(30)))
        rows = self._rendered([tall, AskUserOption(title="Other")])

        assert any("lines hidden" in r for r in rows)
        # The box stays pinned to the option list rather than growing to 30 rows.
        assert len(rows) < 20

    def test_no_preview_column_on_a_narrow_terminal(self):
        out = "\n".join(self._rendered(self._opts(), width=50))

        assert "┌" not in out  # falls back to a full-width option list
        assert "Tabs" in out


class TestFreeformRowLayout:
    """The 'Type something…' row carries no marker of its own and lines up
    with the real options — the only arrow on screen is the moving cursor."""

    def _rows(self, multi=False, keys=(), width=60):
        c, _ = _component(
            options=[AskUserOption(title="Option A"), AskUserOption(title="Option B")],
            allow_multiple=multi,
        )
        for k in keys:
            c.handle_input(_key(k, char=k if len(k) == 1 else None))
        buf = Buffer.empty(Rect(0, 0, width, 24))
        rows = c.render_cells(Rect(0, 0, width, 24), buf)
        return [
            "".join(buf.get(x, y).symbol for x in range(width)).rstrip() for y in range(rows)
        ]

    def _col_of(self, rows, needle):
        return next(r.index(needle) for r in rows if needle in r)

    def test_no_arrow_when_the_row_is_not_the_cursor(self):
        rows = self._rows()
        freeform = next(r for r in rows if "Type something" in r)

        assert "❯" not in freeform

    def test_label_aligns_with_the_option_titles(self):
        rows = self._rows()

        assert self._col_of(rows, "Type something") == self._col_of(rows, "Option A")

    def test_alignment_holds_when_the_cursor_is_on_the_row(self):
        rows = self._rows(keys=["down", "down"])
        freeform = next(r for r in rows if "Type something" in r)

        assert freeform.count("❯") == 1  # the cursor only, not a second marker
        assert self._col_of(rows, "Type something") == self._col_of(rows, "Option A")

    def test_multi_select_tick_shares_the_checkbox_column(self):
        rows = self._rows(multi=True, keys=["space", "down", "down", "space", "h", "i", "enter"])

        assert self._col_of(rows, "Type something") == self._col_of(rows, "Option A")
        # The saved-text tick sits under the options' checkboxes.
        option_box = next(r for r in rows if "Option A" in r).index("✔")
        freeform_box = next(r for r in rows if "Type something" in r).index("✔")
        assert option_box == freeform_box


class TestTabCycling:
    """Tab cycles every tab including Review; Shift+Tab goes back."""

    def _seq(self):
        children = [
            _component(question="Q0", options=[AskUserOption(title="A")])[0],
            # No options → opens straight into the editor, the case where Tab
            # used to be swallowed entirely.
            _component(question="Q1", options=[], multiline=True)[0],
        ]
        seq = _AskUserSequence(
            headers=["Multi", "Freeform"], children=children, on_done=lambda v: None
        )
        return seq, children

    def _tab(self, seq, shift=False):
        seq.handle_input(KeyEvent(key="tab", char=None, shift=shift))

    def _pos(self, seq):
        return "review" if seq._on_review else seq._index

    def test_tab_walks_forward_through_review_and_wraps(self):
        seq, _ = self._seq()

        seen = []
        for _ in range(3):
            self._tab(seq)
            seen.append(self._pos(seq))

        assert seen == [1, "review", 0]

    def test_shift_tab_walks_backwards(self):
        seq, _ = self._seq()

        seen = []
        for _ in range(3):
            self._tab(seq, shift=True)
            seen.append(self._pos(seq))

        assert seen == ["review", 1, 0]

    def test_tab_escapes_a_question_that_is_only_an_editor(self):
        seq, children = self._seq()
        self._tab(seq)

        assert children[1].is_editing  # no options, so the editor has focus
        self._tab(seq)

        assert self._pos(seq) == "review"

    def test_arrows_still_belong_to_the_editor(self):
        seq, children = self._seq()
        self._tab(seq)
        assert children[1].is_editing

        seq.handle_input(KeyEvent(key="left", char=None))

        assert self._pos(seq) == 1  # unchanged — the text cursor moved instead

    def test_tabbing_away_keeps_what_was_typed(self):
        seq, children = self._seq()
        self._tab(seq)
        for ch in "hello":
            seq.handle_input(KeyEvent(key=ch, char=ch))

        self._tab(seq)

        assert seq._answers[1] == {"kind": "freeform", "text": "hello"}
        # Coming back resumes the same buffer rather than starting over.
        self._tab(seq, shift=True)
        assert children[1]._ml_lines == ["hello"]

    def test_an_untouched_editor_records_nothing(self):
        seq, _ = self._seq()
        self._tab(seq)
        self._tab(seq)

        assert 1 not in seq._answers


class TestDividers:
    """Question / answers / key hints are separated by full-width rules,
    matching the title-divider-list-divider-hint layout of the pickers."""

    def _rows(self, width=72, **kwargs):
        c, _ = _component(**kwargs)
        buf = Buffer.empty(Rect(0, 0, width, 26))
        rows = c.render_cells(Rect(0, 0, width, 26), buf)
        return [
            "".join(buf.get(x, y).symbol for x in range(width)).rstrip() for y in range(rows)
        ]

    def _rule_rows(self, rows, width=72):
        return [i for i, r in enumerate(rows) if r == "─" * width]

    def test_one_rule_under_the_question_and_one_above_the_hints(self):
        rows = self._rows()
        rules = self._rule_rows(rows)

        assert len(rules) == 2
        question_row = next(i for i, r in enumerate(rows) if "Pick one" in r)
        hint_row = next(i for i, r in enumerate(rows) if "Esc cancel" in r)
        assert rules[0] == question_row + 1
        assert rules[1] == hint_row - 1

    def test_options_sit_between_the_rules(self):
        rows = self._rows()
        first, second = self._rule_rows(rows)
        between = "\n".join(rows[first + 1 : second])

        assert "Alpha" in between and "Beta" in between

    def test_rules_span_the_full_width(self):
        rows = self._rows(width=50)
        assert self._rule_rows(rows, width=50)

    def test_the_editor_gets_the_same_treatment(self):
        rows = self._rows(options=[], multiline=True)
        rules = self._rule_rows(rows)

        assert len(rules) == 2
        assert "Enter to submit" in rows[rules[1] + 1]
