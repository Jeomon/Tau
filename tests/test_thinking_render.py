from tau.message.types import AssistantMessage, ThinkingContent
from tau.modes.interactive.components.message_list import MessageBlock, MessageList
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi


def _plain(lines: list[str]) -> list[str]:
    return [strip_ansi(line) for line in lines]


def test_short_thinking_renders_without_expand_hint() -> None:
    message = AssistantMessage(contents=[ThinkingContent(content="one\ntwo")])
    block = MessageBlock(message, theme=MessageTheme(), tool_result_preview_lines=5)

    lines = _plain(block.render(80))

    assert any("one" in line for line in lines)
    assert all("ctrl+o" not in line for line in lines)


def test_long_thinking_uses_collapsed_detail_preview() -> None:
    content = "\n".join(f"thought {index}" for index in range(7))
    message = AssistantMessage(contents=[ThinkingContent(content=content)])
    block = MessageBlock(message, theme=MessageTheme(), tool_result_preview_lines=5)

    lines = _plain(block.render(80))

    assert any("thought 4" in line for line in lines)
    assert all("thought 5" not in line for line in lines)
    assert any("ctrl+o to expand" in line for line in lines)


def test_ctrl_o_detail_toggle_expands_thinking() -> None:
    content = "\n".join(f"thought {index}" for index in range(7))
    message = AssistantMessage(contents=[ThinkingContent(content=content)])
    messages = MessageList(tool_result_preview_lines=5)
    block = messages.add_message(message)

    messages.toggle_details_expanded()
    lines = _plain(block.render(80))

    assert any("thought 6" in line for line in lines)
    assert any("ctrl+o to collapse" in line for line in lines)


def test_hidden_thinking_stays_hidden() -> None:
    theme = MessageTheme(show_thinking=False)
    message = AssistantMessage(contents=[ThinkingContent(content="private thought")])
    block = MessageBlock(message, theme=theme)

    lines = _plain(block.render(80))
    assert all("private thought" not in line for line in lines)
    assert all("ctrl+o" not in line for line in lines)
