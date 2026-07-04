from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tau.tui.style import Style
from tau.tui.utils import BOLD, ITALIC, RESET, fg

# A color function wraps a string in ANSI codes and returns the styled string.
# Only `MessageTheme.diff_inverse` still uses this shape (see its field comment
# for why); every other color-bearing field below is a structured `Style`,
# applied via `tau.tui.style.apply_style(style, text)`.
ColorFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Color-function builders — standalone ANSI-wrapping helpers, independent of
# the Style-based theme fields below (kept for diff_inverse-style needs and
# anyone building a raw ColorFn directly).
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


# ---------------------------------------------------------------------------
# Per-component theme dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SpinnerTheme:
    """Controls the animated spinner appearance."""

    frames: list[str] = field(default_factory=lambda: ["▖", "▘", "▝", "▗"])
    interval_ms: int = 120
    frame_color: Style = field(default_factory=lambda: Style().with_fg("bright_cyan"))
    label_color: Style = field(default_factory=Style)
    label_thinking: str = "Thinking…"
    label_streaming: str = "Streaming…"
    label_tool_calling: str = "Tool Calling…"
    label_compacting: str = "Compacting…"


@dataclass
class MarkdownTheme:
    """Controls colours for rendered markdown inside assistant messages."""

    heading: Style = field(default_factory=lambda: Style().bold().with_fg("bright_cyan"))
    code_inline: Style = field(default_factory=lambda: Style().with_fg("bright_yellow"))
    code_block: Style = field(default_factory=lambda: Style().with_fg("bright_green"))
    code_block_border: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    # Pygments style for syntax-highlighted fenced code blocks; "" disables
    # highlighting (falls back to the flat `code_block` colour).
    code_syntax_style: str = "monokai"
    quote: Style = field(default_factory=lambda: Style().italic())
    quote_border: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    hr: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    list_bullet: Style = field(default_factory=lambda: Style().with_fg("bright_cyan"))
    bold: Style = field(default_factory=lambda: Style().bold())
    italic: Style = field(default_factory=lambda: Style().italic())
    strikethrough: Style = field(default_factory=lambda: Style().strikethrough())
    link_text: Style = field(default_factory=lambda: Style().with_fg("bright_cyan"))
    link_url: Style = field(default_factory=lambda: Style().with_fg("bright_black"))


@dataclass
class MessageTheme:
    """Controls all colors used when rendering chat messages."""

    you_label: Style = field(default_factory=lambda: Style().bold().with_fg("bright_cyan"))
    assistant_label: Style = field(default_factory=lambda: Style().bold().with_fg("bright_green"))
    tool_arrow: Style = field(default_factory=lambda: Style().with_fg("bright_yellow"))
    tool_result_ok: Style = field(default_factory=Style)
    tool_result_err: Style = field(default_factory=lambda: Style().with_fg("bright_red"))
    thinking: Style = field(default_factory=lambda: Style().dim().italic())
    error_label: Style = field(default_factory=lambda: Style().bold().with_fg("bright_red"))
    dim: Style = field(default_factory=lambda: Style().dim())
    stream_cursor: Style = field(default_factory=lambda: Style().with_fg("bright_white"))
    diff_added: Style = field(default_factory=lambda: Style().with_fg("bright_green"))
    diff_removed: Style = field(default_factory=lambda: Style().with_fg("bright_red"))
    diff_context: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    diff_hunk: Style = field(default_factory=lambda: Style().with_fg("bright_yellow"))
    # Word-diff highlight applied *inside* an already-colored removed/added
    # line (see message_list.py's render_diff call). Must stay a ColorFn: it
    # toggles reverse-video on then back off (`\x1b[27m`), not a full reset —
    # a full reset here would also clear the enclosing line's color for
    # everything after the highlighted word. Style's close is always a full
    # reset (see apply_style), so this one case doesn't fit that model.
    diff_inverse: ColorFn = field(
        default_factory=lambda: (lambda s: "\x1b[7m" + s + "\x1b[27m")
    )
    # Semantic colour roles exposed to tool render_result() callbacks via
    # ToolRenderOptions.theme. Defaults mirror LayoutTheme's roles; when this
    # MessageTheme is part of a LayoutTheme they are overwritten from the
    # layout-level roles in LayoutTheme.__post_init__ so a custom theme's roles
    # reach tool renderers (and extensions using the documented API).
    muted: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    emphasis: Style = field(default_factory=lambda: Style().bold().with_fg("bright_white"))
    success: Style = field(default_factory=lambda: Style().with_fg("green"))
    error: Style = field(default_factory=lambda: Style().bold().with_fg("bright_red"))
    warning: Style = field(default_factory=lambda: Style().with_fg("bright_yellow"))
    accent: Style = field(default_factory=lambda: Style().with_fg("cyan"))
    markdown: MarkdownTheme = field(default_factory=MarkdownTheme)
    show_thinking: bool = True
    show_tool_calls: bool = True
    show_images: bool = True
    thinking_label: str = "thinking…"


@dataclass
class InputTheme:
    """Controls the text-input prompt appearance."""

    prefix: str = "❯ "
    placeholder: str = ""


@dataclass
class SelectListTheme:
    """Controls appearance of the SelectList / CommandPalette component."""

    selected_label: Style = field(default_factory=lambda: Style().bold().with_fg("bright_white"))
    selected_desc: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    normal_label: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    normal_desc: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    indicator: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    empty: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    # Emphasised entry — e.g. directories in the file picker. Lists that have no
    # such distinction simply ignore it.
    selected_dir: Style = field(default_factory=lambda: Style().bold().with_fg("cyan"))
    # Optional full-line background for the selected row (None = no background)
    selected_bg: Style | None = None


@dataclass
class LayoutTheme:
    """
    Top-level theme that wires together all sub-themes.

    Pass a custom instance to App.create() or Layout() to restyle the whole UI:

        from tau.tui.theme import LayoutTheme, SpinnerTheme
        from tau.tui.style import Style

        theme = LayoutTheme(
            divider=Style().with_fg("bright_magenta"),
            spinner=SpinnerTheme(
                frames=["◐", "◓", "◑", "◒"],
                interval_ms=100,
            ),
        )
        app = await App.create(config, theme=theme)
    """

    divider: Style = field(default_factory=lambda: Style().with_fg("bright_black"))
    divider_command: Style = field(default_factory=lambda: Style().with_fg("bright_cyan"))
    divider_execute: Style = field(default_factory=lambda: Style().with_fg("bright_yellow"))

    # Shared semantic roles used by selectors,
    # modals, and other chrome so a single theme key recolours them everywhere.
    muted: Style = field(  # dim chrome/secondary text
        default_factory=lambda: Style().with_fg("bright_black")
    )
    emphasis: Style = field(  # highlighted/active item
        default_factory=lambda: Style().bold().with_fg("bright_white")
    )
    success: Style = field(default_factory=lambda: Style().with_fg("green"))  # positive / current
    error: Style = field(default_factory=lambda: Style().bold().with_fg("bright_red"))
    warning: Style = field(  # caution / highlight
        default_factory=lambda: Style().with_fg("bright_yellow")
    )
    accent: Style = field(default_factory=lambda: Style().with_fg("cyan"))  # highlighted value/path
    border: Style = field(  # modal/box borders
        default_factory=lambda: Style().with_fg("bright_black")
    )

    # Optional terminal background colour applied via OSC 11 when Tau starts.
    # Use a CSS hex string e.g. "#1e1e2e" or an "rgb(r,g,b)" string.
    # None (default) leaves the terminal's own background unchanged.
    terminal_bg: str | None = None

    spinner: SpinnerTheme = field(default_factory=SpinnerTheme)
    message: MessageTheme = field(default_factory=MessageTheme)
    input: InputTheme = field(default_factory=InputTheme)
    select_list: SelectListTheme = field(default_factory=SelectListTheme)

    def __post_init__(self) -> None:
        # Tool render_result() callbacks receive the MessageTheme (via
        # ToolRenderOptions.theme). Mirror the layout-level semantic roles onto
        # it so a custom theme's roles reach tool renderers and the documented
        # theme.muted/.error/.warning/.success/.accent/.emphasis all resolve —
        # keeping "one theme key recolours everywhere" true for renderers too.
        self.message.muted = self.muted
        self.message.emphasis = self.emphasis
        self.message.success = self.success
        self.message.error = self.error
        self.message.warning = self.warning
        self.message.accent = self.accent
