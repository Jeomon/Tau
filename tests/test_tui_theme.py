"""Tests for tau/tui/theme.py — theme dataclasses and color helpers."""

from __future__ import annotations

import re

from tau.tui.theme import (
    InputTheme,
    LayoutTheme,
    MarkdownTheme,
    MessageTheme,
    SelectListTheme,
    SpinnerTheme,
    color,
    rgb,
    rgb_bold,
    rgb_italic,
)


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


class TestColorHelpers:
    def test_color_wraps_text(self):
        fn = color("\x1b[32m")
        result = fn("hello")
        assert "hello" in result
        assert "\x1b[32m" in result

    def test_color_resets_after(self):
        fn = color("\x1b[32m")
        result = fn("hi")
        assert result.endswith("\x1b[0m")

    def test_rgb_produces_truecolor(self):
        fn = rgb(255, 128, 0)
        result = fn("text")
        assert "text" in result
        assert "\x1b[" in result

    def test_rgb_bold_includes_bold(self):
        fn = rgb_bold(100, 200, 50)
        result = fn("bold text")
        assert "bold text" in result
        assert "\x1b[1m" in result

    def test_rgb_italic_includes_italic(self):
        fn = rgb_italic(100, 200, 50)
        result = fn("italic text")
        assert "italic text" in result
        assert "\x1b[3m" in result


class TestSpinnerTheme:
    def test_default_frames(self):
        t = SpinnerTheme()
        assert isinstance(t.frames, list)
        assert len(t.frames) > 0

    def test_default_interval(self):
        t = SpinnerTheme()
        assert t.interval_ms > 0

    def test_default_labels(self):
        t = SpinnerTheme()
        assert t.label_thinking
        assert t.label_streaming
        assert t.label_tool_calling
        assert t.label_compacting

    def test_custom_frames(self):
        t = SpinnerTheme(frames=["◐", "◓", "◑", "◒"])
        assert t.frames == ["◐", "◓", "◑", "◒"]

    def test_frame_color_fn_callable(self):
        t = SpinnerTheme()
        result = t.frame_color("spin")
        assert "spin" in result


class TestMarkdownTheme:
    def test_construction_no_args(self):
        t = MarkdownTheme()
        assert t is not None

    def test_heading_fn_callable(self):
        t = MarkdownTheme()
        result = t.heading("Title")
        assert "Title" in result

    def test_code_inline_fn_callable(self):
        t = MarkdownTheme()
        result = t.code_inline("code")
        assert "code" in result

    def test_bold_fn_callable(self):
        t = MarkdownTheme()
        result = t.bold("bold text")
        assert "bold text" in result

    def test_italic_fn_callable(self):
        t = MarkdownTheme()
        result = t.italic("italic text")
        assert "italic text" in result

    def test_link_text_fn_callable(self):
        t = MarkdownTheme()
        assert "link" in t.link_text("link")

    def test_code_syntax_style_default(self):
        t = MarkdownTheme()
        assert isinstance(t.code_syntax_style, str)
        assert len(t.code_syntax_style) > 0


class TestMessageTheme:
    def test_construction(self):
        t = MessageTheme()
        assert t is not None

    def test_show_thinking_default(self):
        t = MessageTheme()
        assert t.show_thinking is True

    def test_show_tool_calls_default(self):
        t = MessageTheme()
        assert t.show_tool_calls is True

    def test_show_images_default(self):
        t = MessageTheme()
        assert t.show_images is True

    def test_you_label_fn_callable(self):
        t = MessageTheme()
        result = t.you_label("You")
        assert "You" in result

    def test_assistant_label_fn_callable(self):
        t = MessageTheme()
        result = t.assistant_label("Assistant")
        assert "Assistant" in result

    def test_has_markdown_subtheme(self):
        t = MessageTheme()
        assert isinstance(t.markdown, MarkdownTheme)

    def test_diff_added_fn(self):
        t = MessageTheme()
        result = t.diff_added("+added line")
        assert "+added line" in result

    def test_diff_removed_fn(self):
        t = MessageTheme()
        result = t.diff_removed("-removed line")
        assert "-removed line" in result


class TestInputTheme:
    def test_default_prefix(self):
        t = InputTheme()
        assert t.prefix == "❯ "

    def test_default_placeholder(self):
        t = InputTheme()
        assert isinstance(t.placeholder, str)

    def test_custom_prefix(self):
        t = InputTheme(prefix="> ")
        assert t.prefix == "> "


class TestSelectListTheme:
    def test_construction(self):
        t = SelectListTheme()
        assert t is not None

    def test_selected_label_fn(self):
        t = SelectListTheme()
        result = t.selected_label("option")
        assert "option" in result

    def test_normal_label_fn(self):
        t = SelectListTheme()
        result = t.normal_label("option")
        assert "option" in result

    def test_selected_bg_default_none(self):
        t = SelectListTheme()
        assert t.selected_bg is None


class TestLayoutTheme:
    def test_construction(self):
        t = LayoutTheme()
        assert t is not None

    def test_has_spinner(self):
        t = LayoutTheme()
        assert isinstance(t.spinner, SpinnerTheme)

    def test_has_message(self):
        t = LayoutTheme()
        assert isinstance(t.message, MessageTheme)

    def test_has_input(self):
        t = LayoutTheme()
        assert isinstance(t.input, InputTheme)

    def test_has_select_list(self):
        t = LayoutTheme()
        assert isinstance(t.select_list, SelectListTheme)

    def test_divider_fn_callable(self):
        t = LayoutTheme()
        result = t.divider("─────")
        assert "─────" in result

    def test_custom_spinner(self):
        custom = SpinnerTheme(frames=["X", "Y"])
        t = LayoutTheme(spinner=custom)
        assert t.spinner.frames == ["X", "Y"]

    def test_independent_instances(self):
        t1 = LayoutTheme()
        t2 = LayoutTheme()
        t1.input.prefix = "modified"
        assert t2.input.prefix != "modified"


# The semantic roles documented on ToolRenderOptions.theme (tau/tool/types.py)
# must exist on the MessageTheme that tool render_result() callbacks actually
# receive — otherwise a renderer following the docs raises AttributeError inside
# the render callback and silently freezes the TUI.
_TOOL_RENDER_ROLES = ["muted", "emphasis", "success", "error", "warning", "accent"]


class TestToolRenderThemeContract:
    def test_message_theme_exposes_documented_roles(self):
        m = MessageTheme()
        for role in _TOOL_RENDER_ROLES:
            fn = getattr(m, role)
            assert callable(fn)
            assert "x" in str(fn("x"))

    def test_layout_theme_message_exposes_documented_roles(self):
        m = LayoutTheme().message
        for role in _TOOL_RENDER_ROLES:
            assert callable(getattr(m, role))

    def test_custom_layout_role_propagates_to_message(self):
        t = LayoutTheme(muted=lambda s: "<M>" + s + "</M>")
        # LayoutTheme.__post_init__ mirrors layout roles onto .message so tool
        # renderers pick up a custom theme's colours.
        assert t.message.muted("hi") == "<M>hi</M>"
