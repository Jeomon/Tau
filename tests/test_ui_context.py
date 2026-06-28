from __future__ import annotations

from tau.tui.ui_context import UIContext


class _Input:
    def __init__(self) -> None:
        self.text = ""

    def set_text(self, text: str) -> None:
        self.text = text

    def clear(self) -> None:
        self.text = ""

    def insert_at_cursor(self, text: str) -> None:
        self.text += text

    def backspace(self) -> None:
        self.text = self.text[:-1]


class _Layout:
    def __init__(self) -> None:
        self.input = _Input()
        self.refreshes = 0

    def refresh_input_state(self) -> None:
        self.refreshes += 1


def test_programmatic_input_mutations_refresh_picker_state() -> None:
    layout = _Layout()
    ui = UIContext(layout)  # type: ignore[arg-type]

    ui.set_input_text("/peer")
    ui.insert_input_text(" ")
    ui.backspace_input()
    ui.clear_input()

    assert layout.input.text == ""
    assert layout.refreshes == 4
