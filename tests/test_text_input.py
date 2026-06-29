from tau.tui.components.text_input import TextInput
from tau.tui.input import KeyEvent


def test_ctrl_e_moves_to_line_end_when_editor_has_text() -> None:
    editor = TextInput()
    editor.set_text("text")
    editor.handle_input(KeyEvent(key="home"))

    assert editor.handle_input(KeyEvent(key="e", ctrl=True)) is True
    assert editor.cursor == len(editor.text)


def test_ctrl_e_falls_through_when_editor_is_empty() -> None:
    editor = TextInput()

    assert editor.handle_input(KeyEvent(key="e", ctrl=True)) is False
