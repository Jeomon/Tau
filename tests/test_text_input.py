from collections.abc import Iterator

import pytest

import tau.tui.input as input_module
from tau.tui.components.text_input import TextInput
from tau.tui.input import KeyEvent, configure_keybindings


@pytest.fixture(autouse=True)
def restore_keybindings() -> Iterator[None]:
    original = input_module._keybindings_instance
    yield
    input_module._keybindings_instance = original


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
