from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from tau.tui.ansi import (
    RESET, BOLD, DIM, ITALIC,
    BRIGHT_CYAN, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_RED, BRIGHT_WHITE, BRIGHT_BLACK,
    fg,
)

# A color function wraps a string in ANSI codes and returns the styled string.
ColorFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Color-function builders — public helpers for writing custom themes
# ---------------------------------------------------------------------------

def color(ansi_code: str) -> ColorFn:
    """Wrap text in any ANSI SGR code followed by RESET."""
    return lambda s: ansi_code + s + RESET


def rgb(r: int, g: int, b: int) -> ColorFn:
    """Truecolor (24-bit) foreground ColorFn."""
    return lambda s: fg(r, g, b) + s + RESET


def rgb_bold(r: int, g: int, b: int) -> ColorFn:
    """Bold + truecolor foreground ColorFn."""
    return lambda s: BOLD + fg(r, g, b) + s + RESET


def rgb_italic(r: int, g: int, b: int) -> ColorFn:
    """Italic + truecolor foreground ColorFn."""
    return lambda s: ITALIC + fg(r, g, b) + s + RESET


def _wrap(code: str) -> ColorFn:
    return color(code)


# ---------------------------------------------------------------------------
# Per-component theme dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SpinnerTheme:
    """Controls the animated spinner appearance."""
    frames: list[str]        = field(default_factory=lambda: ["▖", "▘", "▝", "▗"])
    interval_ms: int         = 120
    frame_color: ColorFn     = field(default_factory=lambda: _wrap(BRIGHT_CYAN))
    label_color: ColorFn     = field(default_factory=lambda: lambda s: s)
    label_thinking:   str    = "Thinking…"
    label_streaming:  str    = "Streaming…"
    label_tool_calling: str  = "Tool Calling…"
    label_compacting: str    = "Compacting…"


@dataclass
class MarkdownTheme:
    """Controls colours for rendered markdown inside assistant messages."""
    heading:         ColorFn = field(default_factory=lambda: lambda s: BOLD + BRIGHT_CYAN  + s + RESET)
    code_inline:     ColorFn = field(default_factory=lambda: _wrap(BRIGHT_YELLOW))
    code_block:      ColorFn = field(default_factory=lambda: _wrap(BRIGHT_GREEN))
    code_block_border: ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    # Pygments style for syntax-highlighted fenced code blocks; "" disables
    # highlighting (falls back to the flat `code_block` colour).
    code_syntax_style: str = "monokai"
    quote:           ColorFn = field(default_factory=lambda: lambda s: "\x1b[3m" + s + RESET)   # italic
    quote_border:    ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    hr:              ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    list_bullet:     ColorFn = field(default_factory=lambda: _wrap(BRIGHT_CYAN))
    bold:            ColorFn = field(default_factory=lambda: lambda s: BOLD + s + RESET)
    italic:          ColorFn = field(default_factory=lambda: lambda s: "\x1b[3m" + s + RESET)
    strikethrough:   ColorFn = field(default_factory=lambda: lambda s: "\x1b[9m" + s + RESET)
    link_text:       ColorFn = field(default_factory=lambda: _wrap(BRIGHT_CYAN))
    link_url:        ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))


@dataclass
class MessageTheme:
    """Controls all colors used when rendering chat messages."""
    you_label:       ColorFn = field(default_factory=lambda: lambda s: BOLD + BRIGHT_CYAN  + s + RESET)
    assistant_label: ColorFn = field(default_factory=lambda: lambda s: BOLD + BRIGHT_GREEN + s + RESET)
    tool_arrow:      ColorFn = field(default_factory=lambda: _wrap(BRIGHT_YELLOW))
    tool_result_ok:  ColorFn = field(default_factory=lambda: lambda s: s)
    tool_result_err: ColorFn = field(default_factory=lambda: _wrap(BRIGHT_RED))
    thinking:        ColorFn = field(default_factory=lambda: lambda s: DIM + ITALIC + s + RESET)
    error_label:     ColorFn = field(default_factory=lambda: lambda s: BOLD + BRIGHT_RED   + s + RESET)
    dim:             ColorFn = field(default_factory=lambda: _wrap(DIM))
    stream_cursor:   ColorFn = field(default_factory=lambda: _wrap(BRIGHT_WHITE))
    diff_added:      ColorFn = field(default_factory=lambda: _wrap(BRIGHT_GREEN))
    diff_removed:    ColorFn = field(default_factory=lambda: _wrap(BRIGHT_RED))
    diff_context:    ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    diff_hunk:       ColorFn = field(default_factory=lambda: _wrap(BRIGHT_YELLOW))
    diff_inverse:    ColorFn = field(default_factory=lambda: lambda s: "\x1b[7m" + s + "\x1b[27m")
    markdown:        MarkdownTheme = field(default_factory=MarkdownTheme)
    show_thinking:   bool = True
    show_tool_calls: bool = True
    show_images:     bool = True
    thinking_label:  str  = "thinking…"


@dataclass
class InputTheme:
    """Controls the text-input prompt appearance."""
    prefix:      str = "❯ "
    placeholder: str = ""


@dataclass
class SelectListTheme:
    """Controls appearance of the SelectList / CommandPalette component."""
    selected_label: ColorFn = field(default_factory=lambda: lambda s: BOLD + BRIGHT_WHITE + s + RESET)
    selected_desc:  ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    normal_label:   ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    normal_desc:    ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    indicator:      ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    empty:          ColorFn = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    # Optional full-line background for the selected row (None = no background)
    selected_bg: ColorFn | None = field(default_factory=lambda: None)


@dataclass
class LayoutTheme:
    """
    Top-level theme that wires together all sub-themes.

    Pass a custom instance to App.create() or Layout() to restyle the whole UI:

        from tau.tui.theme import LayoutTheme, SpinnerTheme, MessageTheme
        from tau.tui.ansi import BRIGHT_MAGENTA, RESET

        theme = LayoutTheme(
            divider=lambda s: BRIGHT_MAGENTA + s + RESET,
            spinner=SpinnerTheme(
                frames=["◐", "◓", "◑", "◒"],
                interval_ms=100,
            ),
        )
        app = await App.create(config, theme=theme)
    """
    divider:         ColorFn         = field(default_factory=lambda: _wrap(BRIGHT_BLACK))
    divider_command: ColorFn         = field(default_factory=lambda: _wrap(BRIGHT_CYAN))
    divider_execute: ColorFn         = field(default_factory=lambda: _wrap(BRIGHT_YELLOW))
    spinner:         SpinnerTheme    = field(default_factory=SpinnerTheme)
    message:         MessageTheme    = field(default_factory=MessageTheme)
    input:           InputTheme      = field(default_factory=InputTheme)
    select_list:     SelectListTheme = field(default_factory=SelectListTheme)
