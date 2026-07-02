import asyncio
import time
from collections.abc import Iterator

import pytest

import tau.tui.input as input_module
from tau.tui.components.text_input import TextInput
from tau.tui.input import KeyEvent, configure_keybindings
from tau.tui.utils import is_window_focused, set_window_focused


@pytest.fixture(autouse=True)
def restore_keybindings() -> Iterator[None]:
    original = input_module._keybindings_instance
    yield
    input_module._keybindings_instance = original


@pytest.fixture(autouse=True)
def restore_window_focus() -> Iterator[None]:
    original = is_window_focused()
    yield
    set_window_focused(original)


def test_ctrl_e_moves_to_line_end_when_editor_has_text() -> None:
    editor = TextInput()
    editor.set_text("text")
    editor.handle_input(KeyEvent(key="home"))

    assert editor.handle_input(KeyEvent(key="e", ctrl=True)) is True
    assert editor.cursor == len(editor.text)


def test_ctrl_e_falls_through_when_editor_is_empty() -> None:
    editor = TextInput()

    assert editor.handle_input(KeyEvent(key="e", ctrl=True)) is False


def test_submit_uses_configured_binding() -> None:
    configure_keybindings({"tui.input.submit": ["ctrl+s"]})
    submitted: list[str] = []
    editor = TextInput()
    editor.on_submit = submitted.append
    editor.set_text("hello")

    assert editor.handle_input(KeyEvent(key="enter")) is False
    assert editor.handle_input(KeyEvent(key="s", ctrl=True)) is True
    assert submitted == ["hello"]


def test_newline_uses_configured_binding() -> None:
    configure_keybindings({"tui.input.newline": ["ctrl+j"]})
    editor = TextInput()
    editor.set_text("hello")

    assert editor.handle_input(KeyEvent(key="j", ctrl=True)) is True
    assert editor.text == "hello\n"


def test_followup_and_dequeue_use_configured_bindings() -> None:
    configure_keybindings(
        {
            "app.message.followup": ["ctrl+f"],
            "app.message.dequeue": ["ctrl+r"],
        }
    )
    followups: list[str] = []
    dequeues: list[bool] = []
    editor = TextInput()
    editor.on_followup = followups.append
    editor.on_dequeue = lambda: dequeues.append(True)
    editor.set_text("later")

    assert editor.handle_input(KeyEvent(key="f", ctrl=True)) is True
    assert editor.handle_input(KeyEvent(key="r", ctrl=True)) is True
    assert followups == ["later"]
    assert dequeues == [True]


class TestUpDownSoftWrap:
    """Up/Down should walk soft-wrapped visual rows before touching history."""

    def _wrapped_editor(self, width: int = 20) -> TextInput:
        editor = TextInput()
        editor.replace_history(["first message", "second message"])
        # prefix "> " is 2 cols; render(20) -> available == 18 columns.
        editor.set_text("a" * 10 + " " + "b" * 10 + " " + "c" * 10)
        editor.render(width)
        return editor

    def test_up_from_last_row_moves_within_wrapped_line_first(self) -> None:
        editor = self._wrapped_editor()
        editor._cursor = len(editor.text)  # noqa: SLF001

        text_before = editor.text
        assert editor.handle_input(KeyEvent(key="up")) is True
        assert editor.text == text_before  # history not touched yet
        assert editor.cursor < len(text_before)

    def test_up_falls_through_to_history_only_at_top_row(self) -> None:
        editor = self._wrapped_editor()
        editor._cursor = len(editor.text)  # noqa: SLF001

        editor.handle_input(KeyEvent(key="up"))  # -> top visual row, same line
        assert editor.text != "second message"

        editor.handle_input(KeyEvent(key="up"))  # now at top row -> history
        assert editor.text == "second message"

    def test_down_from_top_row_moves_within_wrapped_line_first(self) -> None:
        editor = self._wrapped_editor()
        editor._cursor = 0  # noqa: SLF001

        text_before = editor.text
        assert editor.handle_input(KeyEvent(key="down")) is True
        assert editor.text == text_before
        assert editor.cursor > 0

    def test_single_line_unwrapped_still_falls_through_immediately(self) -> None:
        editor = TextInput()
        editor.replace_history(["prior"])
        editor.set_text("short")
        editor.render(80)

        assert editor.handle_input(KeyEvent(key="up")) is True
        assert editor.text == "prior"


class TestMouseCursorMovement:
    def test_click_moves_cursor_across_hard_lines(self) -> None:
        editor = TextInput(prefix="> ")
        editor.set_text("first\nsecond")
        editor.render(40)

        assert editor.move_cursor_to_visual(1, 5) is True
        assert editor.cursor == len("first\nsec")

    def test_click_moves_cursor_across_soft_wrapping(self) -> None:
        editor = TextInput(prefix="> ")
        editor.set_text("abcdefgh")
        editor.render(6)  # Four text columns after the prefix.

        assert editor.move_cursor_to_visual(1, 4) is True
        assert editor.cursor == 6

    def test_click_clamps_to_end_of_visual_row(self) -> None:
        editor = TextInput(prefix="> ")
        editor.set_text("abc")
        editor.render(40)

        assert editor.move_cursor_to_visual(0, 30) is True
        assert editor.cursor == 3

    def test_click_outside_editor_is_ignored(self) -> None:
        editor = TextInput()
        editor.set_text("abc")
        editor.render(40)

        assert editor.move_cursor_to_visual(2, 2) is False
        assert editor.cursor == 3


class _FakeTUI:
    def __init__(self) -> None:
        self.render_requests = 0

    def request_render(self) -> None:
        self.render_requests += 1


class TestCursorBlink:
    def test_typing_keeps_cursor_solid(self) -> None:
        editor = TextInput(tui=_FakeTUI())  # type: ignore[arg-type]
        set_window_focused(True)
        editor._blink_on = False  # noqa: SLF001 — simulate mid-blink "off" phase
        editor.handle_input(KeyEvent(key="a"))
        assert editor._blink_on is True  # noqa: SLF001

    def test_idle_and_focused_eventually_blinks(self) -> None:
        editor = TextInput(tui=_FakeTUI())  # type: ignore[arg-type]
        set_window_focused(True)

        async def scenario() -> bool:
            editor.render(40)  # lazily starts the blink task (needs a running loop)
            editor._last_activity = time.monotonic() - 10  # noqa: SLF001 — force "idle"
            for _ in range(20):
                await asyncio.sleep(0.05)
                if not editor._blink_on:  # noqa: SLF001
                    return True
            return False

        toggled = asyncio.run(scenario())
        if editor._blink_task is not None:  # noqa: SLF001
            editor._blink_task.cancel()  # noqa: SLF001
        assert toggled

    def test_unfocused_disables_blink_dimming(self) -> None:
        editor = TextInput(tui=_FakeTUI())  # type: ignore[arg-type]
        editor.set_text("hi")
        set_window_focused(False)
        editor._blink_on = False  # noqa: SLF001

        cell = editor._effective_cursor_cell()  # noqa: SLF001
        assert cell("a") == "a"  # unfocused: defers to cursor_block's own native-cursor path

    def test_custom_cursor_cell_override_bypasses_blink(self) -> None:
        editor = TextInput(tui=_FakeTUI())  # type: ignore[arg-type]
        editor.cursor_cell = lambda ch: f"[{ch}]"
        set_window_focused(True)
        editor._blink_on = False  # noqa: SLF001

        cell = editor._effective_cursor_cell()  # noqa: SLF001
        assert cell("a") == "[a]"  # extension's own cursor rendering is respected as-is

    def test_no_tui_never_starts_blink_task(self) -> None:
        editor = TextInput()
        editor.render(40)
        assert editor._blink_task is None  # noqa: SLF001

    def test_cursor_blink_disabled_never_starts_task_and_stays_solid(self) -> None:
        editor = TextInput(tui=_FakeTUI(), cursor_blink=False)  # type: ignore[arg-type]
        set_window_focused(True)
        editor.render(40)
        assert editor._blink_task is None  # noqa: SLF001

        cell = editor._effective_cursor_cell()  # noqa: SLF001
        assert cell("a") != "a"  # still rendered via cursor_block, permanently solid

    def test_set_cursor_blink_disables_live_and_cancels_running_task(self) -> None:
        editor = TextInput(tui=_FakeTUI())  # type: ignore[arg-type]
        set_window_focused(True)

        async def scenario() -> None:
            editor.render(40)  # starts the blink task (needs a running loop)
            assert editor._blink_task is not None  # noqa: SLF001
            editor._blink_on = False  # noqa: SLF001 — simulate mid-blink "off" phase

            editor.set_cursor_blink(False)

            assert editor._blink_task is None  # noqa: SLF001
            assert editor._blink_on is True  # noqa: SLF001 — forced solid immediately
            editor.render(40)  # must not restart the task while disabled
            assert editor._blink_task is None  # noqa: SLF001

        asyncio.run(scenario())

    def test_set_cursor_blink_re_enables_and_lazily_restarts(self) -> None:
        editor = TextInput(tui=_FakeTUI(), cursor_blink=False)  # type: ignore[arg-type]
        set_window_focused(True)

        async def scenario() -> None:
            editor.render(40)
            assert editor._blink_task is None  # noqa: SLF001

            editor.set_cursor_blink(True)
            editor.render(40)
            assert editor._blink_task is not None  # noqa: SLF001
            editor._blink_task.cancel()  # noqa: SLF001

        asyncio.run(scenario())

    def test_dispose_cancels_cursor_blink_task(self) -> None:
        editor = TextInput(tui=_FakeTUI())  # type: ignore[arg-type]

        async def scenario() -> None:
            editor.render(40)
            task = editor._blink_task  # noqa: SLF001
            assert task is not None

            editor.dispose()
            await asyncio.sleep(0)

            assert editor._blink_task is None  # noqa: SLF001
            assert task.cancelled() or task.done()
            assert editor._tui is None  # noqa: SLF001

        asyncio.run(scenario())
