from tau.message.types import AssistantMessage, ThinkingContent
from tau.modes.interactive.components.message_list import MessageBlock, MessageList
from tau.tui.theme import MessageTheme
from tau.tui.utils import DIM, ITALIC, strip_ansi


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


def test_thinking_renders_markdown() -> None:
    message = AssistantMessage(contents=[ThinkingContent(content="## Plan\n\n- inspect\n- verify")])
    block = MessageBlock(message, theme=MessageTheme(), tool_result_preview_lines=10)

    rendered = block.render(80)
    lines = _plain(rendered)

    assert any("Plan" in line and "##" not in line for line in lines)
    assert any("•" in line and "inspect" in line for line in lines)
    assert all(DIM in line and ITALIC in line for line in rendered if strip_ansi(line).strip())


def test_streaming_incomplete_thinking_markdown_renders() -> None:
    message = AssistantMessage(contents=[ThinkingContent(content="Working on **partial")])
    block = MessageBlock(message, streaming=True, theme=MessageTheme())

    lines = _plain(block.render(80))

    assert any("Working on" in line for line in lines)


def test_thinking_removes_internal_blank_lines() -> None:
    message = AssistantMessage(
        contents=[
            ThinkingContent(
                content=(
                    'The user just said "hi" which is a simple greeting.\n\n'
                    "This does not match any available skill.\n\n"
                    "I should answer concisely."
                )
            )
        ]
    )
    block = MessageBlock(message, theme=MessageTheme(), tool_result_preview_lines=10)

    lines = _plain(block.render(80))
    while lines and not lines[-1].strip():
        lines.pop()

    assert all(line.strip() for line in lines)
