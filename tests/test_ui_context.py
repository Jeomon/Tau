from __future__ import annotations

from tau.modes.interactive.ui_context import UIContext


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


class _TUI:
    def __init__(self) -> None:
        self.renders = 0

    def request_render(self) -> None:
        self.renders += 1


class _Layout:
    def __init__(self) -> None:
        self.input = _Input()
        self.refreshes = 0
        self.messages: list[object] = []
        self._tui = _TUI()

    def refresh_input_state(self) -> None:
        self.refreshes += 1

    def add_message(self, message: object, streaming: bool = False) -> None:
        self.messages.append(message)


def test_programmatic_input_mutations_refresh_picker_state() -> None:
    layout = _Layout()
    ui = UIContext(layout)  # type: ignore[arg-type]

    ui.set_input_text("/peer")
    ui.insert_input_text(" ")
    ui.backspace_input()
    ui.clear_input()

    assert layout.input.text == ""
    assert layout.refreshes == 4


class TestCustomMessage:
    """`register_message_renderer` dispatches on CustomMessage.custom_type, but
    until `custom_message` existed nothing an extension could call produced one:
    `notify` forces system/tool, and `append_entry` writes data that is never
    rendered. These pin the two halves together."""

    def _post(self, **kwargs) -> object:
        layout = _Layout()
        ui = UIContext(layout)  # type: ignore[arg-type]
        ui.custom_message(**kwargs)
        assert len(layout.messages) == 1
        assert layout._tui.renders == 1
        return layout.messages[0]

    def test_custom_type_reaches_the_message(self) -> None:
        msg = self._post(custom_type="banner", content="Deploy finished")
        assert msg.custom_type == "banner"  # type: ignore[attr-defined]
        assert msg.contents[0].content == "Deploy finished"  # type: ignore[attr-defined]

    def test_details_are_carried_untouched_for_the_renderer(self) -> None:
        payload = {"status": "green", "count": 3}
        msg = self._post(custom_type="banner", content="ok", details=payload)
        assert msg.details is payload  # type: ignore[attr-defined]

    def test_lines_are_posted_as_prerendered_lines(self) -> None:
        msg = self._post(custom_type="report", content=["one", "two"])
        # A trailing blank keeps the same spacing notify() produces.
        assert msg.contents[0].lines == ["one", "two", ""]  # type: ignore[attr-defined]

    def test_a_registered_renderer_receives_it(self) -> None:
        from tau.tui.markdown import message_renderer_registry

        seen: list[object] = []

        def render(message, theme, width):
            seen.append(message)
            return ["rendered"]

        message_renderer_registry.replace({"banner": render})
        try:
            msg = self._post(custom_type="banner", content="hello")
            assert message_renderer_registry.render(msg, None, 80) == ["rendered"]
        finally:
            message_renderer_registry.replace({})
        assert seen and seen[0] is msg

    def test_unregistered_type_still_renders(self) -> None:
        """No renderer must not mean an invisible message — the registry
        returns None and MessageList falls back to notify's framing."""
        from tau.tui.markdown import message_renderer_registry

        message_renderer_registry.replace({})
        msg = self._post(custom_type="nobody-renders-this", content="still visible")
        assert message_renderer_registry.render(msg, None, 80) is None
        assert msg.contents[0].content == "still visible"  # type: ignore[attr-defined]
