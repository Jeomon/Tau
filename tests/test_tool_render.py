"""Tests for tau/tool/render.py and tau/tool/types.py — tool display utilities."""

from __future__ import annotations

from types import SimpleNamespace

from tau.message.types import ToolMessage, ToolResultContent
from tau.modes.interactive.components.message_list import MessageBlock, _default_shell_preview
from tau.tool.render import call_line, display_name
from tau.tool.types import ToolKind, ToolResult
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi


class TestDisplayName:
    def test_single_word(self):
        assert display_name("read") == "Read"

    def test_snake_case(self):
        assert display_name("read_file") == "Read File"

    def test_three_words(self):
        assert display_name("web_fetch_url") == "Web Fetch Url"

    def test_already_capitalized(self):
        assert display_name("Read") == "Read"

    def test_empty_string(self):
        assert display_name("") == ""

    def test_no_underscore(self):
        assert display_name("grep") == "Grep"


class TestCallLine:
    def test_single_value(self):
        lines = call_line("read_file", "/path/to/file")
        assert len(lines) == 1
        plain = strip_ansi(lines[0])
        assert "Read File" in plain
        assert "/path/to/file" in plain

    def test_multiple_values(self):
        lines = call_line("grep", "pattern", "/path")
        plain = strip_ansi(lines[0])
        assert "pattern" in plain
        assert "/path" in plain

    def test_empty_values_skipped(self):
        lines = call_line("tool", "arg1", "", "arg3")
        plain = strip_ansi(lines[0])
        assert "arg1" in plain
        assert "arg3" in plain
        # empty string should not add extra comma
        assert ",," not in plain

    def test_all_empty_values(self):
        lines = call_line("tool", "", "")
        plain = strip_ansi(lines[0])
        assert "Tool" in plain

    def test_no_values(self):
        lines = call_line("tool")
        assert len(lines) == 1
        plain = strip_ansi(lines[0])
        assert "Tool" in plain


class TestToolResult:
    def test_ok_constructor(self):
        r = ToolResult.ok("call1", "output")
        assert r.id == "call1"
        assert r.content == "output"
        assert r.is_error is False

    def test_error_constructor(self):
        r = ToolResult.error("call1", "something failed")
        assert r.id == "call1"
        assert r.content == "something failed"
        assert r.is_error is True

    def test_ok_with_metadata(self):
        r = ToolResult.ok("c1", "data", metadata={"key": "val"})
        assert r.metadata == {"key": "val"}

    def test_error_with_metadata(self):
        r = ToolResult.error("c1", "err", metadata={"key": "val"})
        assert r.metadata == {"key": "val"}


class TestToolKind:
    def test_kinds_exist(self):
        assert ToolKind.Read
        assert ToolKind.Edit
        assert ToolKind.Write
        assert ToolKind.Execute
        assert ToolKind.Web


class TestDefaultToolResultShell:
    def test_short_output_has_no_expand_hint(self):
        rendered = _default_shell_preview(
            ["one", "two"],
            expanded=False,
            expandable=True,
            preview_lines=5,
            theme=MessageTheme(),
        )
        assert rendered == ["one", "two"]

    def test_long_output_is_collapsed_and_expandable(self):
        rendered = _default_shell_preview(
            [str(index) for index in range(7)],
            expanded=False,
            expandable=True,
            preview_lines=5,
            theme=MessageTheme(),
        )
        assert rendered[:5] == ["0", "1", "2", "3", "4"]
        assert "ctrl+o to expand" in strip_ansi(rendered[-1])

    def test_opt_out_always_shows_complete_output(self):
        lines = [str(index) for index in range(7)]
        rendered = _default_shell_preview(
            lines,
            expanded=False,
            expandable=False,
            preview_lines=2,
            theme=MessageTheme(),
        )
        assert rendered == lines


class TestMarkdownToolResult:
    def test_explicit_markdown_result_is_rendered(self):
        message = ToolMessage.from_result(
            ToolResultContent(
                id="call",
                content="## Result\n\n- first\n- second",
                metadata={"_render_format": "markdown"},
            )
        )

        lines = [strip_ansi(line) for line in MessageBlock(message).render(80)]

        assert any("Result" in line and "##" not in line for line in lines)
        assert any("•" in line and "first" in line for line in lines)

    def test_plain_result_does_not_infer_markdown(self):
        message = ToolMessage.from_result(
            ToolResultContent(id="call", content="## literal heading")
        )

        lines = [strip_ansi(line) for line in MessageBlock(message).render(80)]

        assert any("## literal heading" in line for line in lines)

    def test_custom_renderer_can_opt_into_markdown(self):
        message = ToolMessage.from_result(
            ToolResultContent(
                id="call",
                tool_name="web_fetch",
                content="ignored",
                metadata={"_render_format": "markdown"},
            )
        )
        tool = SimpleNamespace(
            render_result=lambda _content, _opts: ["Fetched example.com", "", "## Article"],
            render_shell="default",
            result_preview_lines=None,
            result_expandable=True,
        )

        lines = [
            strip_ansi(line)
            for line in MessageBlock(
                message,
                tool_lookup=lambda _name: tool,
            ).render(80)
        ]

        assert any("Article" in line and "##" not in line for line in lines)


class TestToolResultExtraBlocks:
    def test_block_can_override_collapsed_preview_lines(self):
        message = ToolMessage.from_result(
            ToolResultContent(
                id="call",
                content="updated file",
                metadata={
                    "_extra_blocks": [
                        {
                            "lines": ["1 error", "ERROR [1:1] broken"],
                            "preview_lines": 1,
                        }
                    ]
                },
            )
        )
        block = MessageBlock(message)

        collapsed = [strip_ansi(line) for line in block.render(80)]

        assert any("1 error" in line for line in collapsed)
        assert not any("broken" in line for line in collapsed)
        assert any("ctrl+o to expand" in line for line in collapsed)

        block.toggle_expanded()
        expanded = [strip_ansi(line) for line in block.render(80)]

        assert sum("broken" in line for line in expanded) == 1
        assert any("ctrl+o to collapse" in line for line in expanded)

    def test_display_content_does_not_replace_model_content(self):
        message = ToolMessage.from_result(
            ToolResultContent(
                id="call",
                content="updated file\n\nERROR [1:1] broken",
                metadata={
                    "_display_content": "updated file",
                    "_extra_blocks": [
                        {
                            "lines": ["1 error", "ERROR [1:1] broken"],
                            "preview_lines": 1,
                        }
                    ],
                },
            )
        )
        block = MessageBlock(message)
        block.toggle_expanded()

        expanded = [strip_ansi(line) for line in block.render(80)]

        assert message.contents[0].content.endswith("ERROR [1:1] broken")
        assert sum("ERROR [1:1] broken" in line for line in expanded) == 1
