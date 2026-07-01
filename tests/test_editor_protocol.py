"""The editor (prompt) protocols and TextInput's conformance to them."""

from __future__ import annotations

from tau.tui.components.editor import EditorComponent, EditorExtras
from tau.tui.components.text_input import TextInput


class TestTextInputConformance:
    def test_textinput_is_editor_component(self):
        assert isinstance(TextInput(), EditorComponent)

    def test_textinput_is_editor_extras(self):
        assert isinstance(TextInput(), EditorExtras)

    def test_public_accessors_delegate_to_private_storage(self):
        ti = TextInput(prefix="> ")
        ti.set_text("hello")
        assert ti.cursor == 5 and ti.text == "hello"
        ti.arg_hint = "<x>"
        assert ti._arg_hint == "<x>"
        ti.prefix = "! "
        assert ti._prefix == "! "
        ti.visual_strip = 1
        assert ti._visual_strip == 1
        cb = lambda _s: None  # noqa: E731
        ti.on_submit = cb
        assert ti._on_submit is cb


class _CoreEditor:
    """A minimal editor: core surface only, no extras."""

    on_submit = on_followup = on_dequeue = None

    def render(self, width: int) -> list[str]:
        return [""]

    def handle_input(self, event) -> bool:
        return False

    @property
    def text(self) -> str:
        return ""

    @property
    def cursor(self) -> int:
        return 0

    def set_text(self, text: str) -> None: ...
    def clear(self) -> None: ...
    def insert_at_cursor(self, text: str) -> None: ...
    def submit(self) -> None: ...


class TestPartialEditor:
    def test_core_only_satisfies_component_not_extras(self):
        ed = _CoreEditor()
        assert isinstance(ed, EditorComponent)
        assert not isinstance(ed, EditorExtras)

    def test_missing_core_member_fails_component(self):
        class NotAnEditor:
            def render(self, width: int) -> list[str]:
                return [""]

            # no handle_input / text / cursor / submit / callbacks

        assert not isinstance(NotAnEditor(), EditorComponent)
