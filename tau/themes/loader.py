from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from tau.tui.ansi import (
    BOLD, DIM, ITALIC, RESET, fg,
    BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, DEFAULT,
    BRIGHT_BLACK, BRIGHT_RED, BRIGHT_GREEN, BRIGHT_YELLOW,
    BRIGHT_BLUE, BRIGHT_MAGENTA, BRIGHT_CYAN, BRIGHT_WHITE,
)
from tau.tui.theme import (
    ColorFn, InputTheme, LayoutTheme, MarkdownTheme,
    MessageTheme, SelectListTheme, SpinnerTheme,
)
from tau.themes.types import LoadThemesResult, ThemeLoadError


# ---------------------------------------------------------------------------
# Color parsing
# ---------------------------------------------------------------------------

# Named ANSI colors → their SGR escape. These map to the terminal's own 16-colour
# palette (not fixed RGB), so a theme written with names adapts to the user's
# terminal colours — the same behaviour as the in-code defaults.
_NAMED_COLORS: dict[str, str] = {
    "black": BLACK, "red": RED, "green": GREEN, "yellow": YELLOW,
    "blue": BLUE, "magenta": MAGENTA, "cyan": CYAN, "white": WHITE,
    "default": DEFAULT,
    "bright_black": BRIGHT_BLACK, "bright_red": BRIGHT_RED,
    "bright_green": BRIGHT_GREEN, "bright_yellow": BRIGHT_YELLOW,
    "bright_blue": BRIGHT_BLUE, "bright_magenta": BRIGHT_MAGENTA,
    "bright_cyan": BRIGHT_CYAN, "bright_white": BRIGHT_WHITE,
}

def _parse_hex(value: str) -> tuple[int, int, int] | None:
    """Parse '#rrggbb' → (r, g, b) or return None."""
    v = value.strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            return int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)
        except ValueError:
            pass
    return None


def _make_color_fn(
    value: Any,
    bold: bool = False,
    italic: bool = False,
    dim: bool = False,
) -> ColorFn | None:
    """
    Convert a JSON color value to a ColorFn.

    Accepts:
      - ``"#rrggbb"``          — plain hex (fixed RGB)
      - ``"bright_cyan"``      — a named ANSI colour (adapts to the terminal palette)
      - ``{"color": "...", "bold": true, "italic": true, "dim": true}``
    """
    if value is None:
        return None

    color_str: str | None = None

    if isinstance(value, str):
        color_str = value
    elif isinstance(value, dict):
        color_str = value.get("color")
        bold   = bold   or bool(value.get("bold",   False))
        italic = italic or bool(value.get("italic", False))
        dim    = dim    or bool(value.get("dim",    False))

    if not color_str:
        return None

    # Prefer a named ANSI colour (terminal-palette aware); fall back to hex.
    code = _NAMED_COLORS.get(color_str.strip().lower())
    if code is None:
        rgb = _parse_hex(color_str)
        if rgb is None:
            return None
        code = fg(*rgb)

    prefix = ""
    if bold:
        prefix += BOLD
    if italic:
        prefix += ITALIC
    if dim:
        prefix += DIM
    prefix += code

    return lambda s, p=prefix: p + s + RESET


def _c(
    colors: dict,
    key: str,
    bold: bool = False,
    italic: bool = False,
    dim: bool = False,
) -> ColorFn | None:
    return _make_color_fn(colors.get(key), bold=bold, italic=italic, dim=dim)


# ---------------------------------------------------------------------------
# Dict → LayoutTheme
# ---------------------------------------------------------------------------

def load_theme_from_dict(data: dict) -> tuple[LayoutTheme | None, str | None]:
    """
    Parse a validated theme dict into a LayoutTheme.
    Returns (theme, None) on success or (None, error_message) on failure.
    """
    name = data.get("name")
    if not name or not isinstance(name, str):
        return None, "missing 'name' field"

    colors: dict      = data.get("colors", {})
    input_cfg: dict   = data.get("input", {})
    spinner_cfg: dict = data.get("spinner", {})

    # Use a default instance to fill in any tokens the file omits
    d = LayoutTheme()

    md = MarkdownTheme(
        heading           = _c(colors, "heading",           bold=True)   or d.message.markdown.heading,
        code_inline       = _c(colors, "code_inline")                    or d.message.markdown.code_inline,
        code_block        = _c(colors, "code_block")                     or d.message.markdown.code_block,
        code_block_border = _c(colors, "code_block_border")              or d.message.markdown.code_block_border,
        code_syntax_style = (data["code_syntax_style"] if "code_syntax_style" in data
                             else d.message.markdown.code_syntax_style),
        quote             = _c(colors, "quote",             italic=True) or d.message.markdown.quote,
        quote_border      = _c(colors, "quote_border")                   or d.message.markdown.quote_border,
        hr                = _c(colors, "hr")                             or d.message.markdown.hr,
        list_bullet       = _c(colors, "list_bullet")                    or d.message.markdown.list_bullet,
        bold              = _c(colors, "bold",              bold=True)   or d.message.markdown.bold,
        italic            = _c(colors, "italic",            italic=True) or d.message.markdown.italic,
        strikethrough     = _c(colors, "strikethrough")                  or d.message.markdown.strikethrough,
        link_text         = _c(colors, "link_text")                      or d.message.markdown.link_text,
        link_url          = _c(colors, "link_url")                       or d.message.markdown.link_url,
    )

    msg = MessageTheme(
        you_label       = _c(colors, "you_label",       bold=True)   or d.message.you_label,
        assistant_label = _c(colors, "assistant_label", bold=True)   or d.message.assistant_label,
        tool_arrow      = _c(colors, "tool_arrow")                   or d.message.tool_arrow,
        tool_result_ok  = _c(colors, "tool_result_ok")               or d.message.tool_result_ok,
        tool_result_err = _c(colors, "tool_result_err")              or d.message.tool_result_err,
        thinking        = _c(colors, "thinking",        italic=True) or d.message.thinking,
        error_label     = _c(colors, "error_label",     bold=True)   or d.message.error_label,
        dim             = _c(colors, "dim")                          or d.message.dim,
        stream_cursor   = _c(colors, "stream_cursor")                or d.message.stream_cursor,
        show_thinking   = bool(data["show_thinking"])   if "show_thinking"   in data else d.message.show_thinking,
        show_tool_calls = bool(data["show_tool_calls"]) if "show_tool_calls" in data else d.message.show_tool_calls,
        markdown        = md,
    )

    raw_frames = spinner_cfg.get("frames")
    spinner = SpinnerTheme(
        frames           = raw_frames if isinstance(raw_frames, list) else d.spinner.frames,
        interval_ms      = int(spinner_cfg["interval_ms"]) if "interval_ms" in spinner_cfg else d.spinner.interval_ms,
        frame_color      = _c(colors, "spinner_frame")                   or d.spinner.frame_color,
        label_color      = _c(colors, "spinner_label")                   or d.spinner.label_color,
        label_thinking   = spinner_cfg.get("label_thinking",   d.spinner.label_thinking),
        label_tool_calling = spinner_cfg.get("label_tool_calling", d.spinner.label_tool_calling),
        label_compacting = spinner_cfg.get("label_compacting", d.spinner.label_compacting),
    )

    select = SelectListTheme(
        selected_label = _c(colors, "selected_label", bold=True) or d.select_list.selected_label,
        selected_desc  = _c(colors, "selected_desc")             or d.select_list.selected_desc,
        normal_label   = _c(colors, "normal_label")              or d.select_list.normal_label,
        normal_desc    = _c(colors, "normal_desc")               or d.select_list.normal_desc,
        indicator      = _c(colors, "indicator")                 or d.select_list.indicator,
        empty          = _c(colors, "empty")                     or d.select_list.empty,
        selected_bg    = _c(colors, "selected_bg")               or d.select_list.selected_bg,
    )

    input_theme = InputTheme(
        prefix      = input_cfg.get("prefix",      d.input.prefix),
        placeholder = input_cfg.get("placeholder", d.input.placeholder),
    )

    layout = LayoutTheme(
        divider     = _c(colors, "divider") or d.divider,
        spinner     = spinner,
        message     = msg,
        input       = input_theme,
        select_list = select,
    )

    return layout, None


# ---------------------------------------------------------------------------
# File / directory loading
# ---------------------------------------------------------------------------

def _parse_theme_file(path: Path) -> tuple[dict | None, str | None]:
    """Read and parse a theme file. Supports .yaml, .yml, and .json."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"read error: {exc}"

    suffix = path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except Exception as exc:
        return None, f"parse error: {exc}"

    if not isinstance(data, dict):
        return None, "expected a mapping at top level"
    return data, None


# Keys the loader actually reads. Used to warn theme authors about typos.
_VALID_TOP_KEYS = frozenset({
    "name", "colors", "input", "spinner", "show_thinking", "show_tool_calls",
    "code_syntax_style",
})
_VALID_COLOR_KEYS = frozenset({
    "assistant_label", "bold", "code_block", "code_block_border", "code_inline",
    "dim", "divider", "empty", "error_label", "heading", "hr", "indicator",
    "italic", "link_text", "link_url", "list_bullet", "normal_desc", "normal_label",
    "quote", "quote_border", "selected_bg", "selected_desc", "selected_label",
    "spinner_frame", "spinner_label", "stream_cursor", "strikethrough", "thinking",
    "tool_arrow", "tool_result_err", "tool_result_ok", "you_label",
})


def _valid_color_value(value: Any) -> bool:
    color = value.get("color") if isinstance(value, dict) else value
    if not isinstance(color, str):
        return False
    return color.strip().lower() in _NAMED_COLORS or _parse_hex(color) is not None


def validate_theme_dict(data: dict) -> list[str]:
    """Return human-readable warnings for typos / invalid values in a theme dict.

    Non-fatal: every issue here still falls back to a sensible default, but
    surfacing them helps theme authors catch mistakes.
    """
    warnings: list[str] = []
    for key in data:
        if key not in _VALID_TOP_KEYS:
            warnings.append(f"unknown key '{key}'")
    colors = data.get("colors", {})
    if isinstance(colors, dict):
        for key, val in colors.items():
            if key not in _VALID_COLOR_KEYS:
                warnings.append(f"unknown color '{key}'")
            elif not _valid_color_value(val):
                warnings.append(f"invalid color value for '{key}': {val!r}")
    return warnings


def load_theme_from_file(path: Path) -> tuple[LayoutTheme | None, str | None]:
    """Load a single theme file (.yaml, .yml, or .json)."""
    data, err = _parse_theme_file(path)
    if err or data is None:
        return None, err
    return load_theme_from_dict(data)


def load_themes_from_dir(directory: Path) -> LoadThemesResult:
    """Load all theme files in a directory (non-recursive). Supports .yaml, .yml, .json."""
    result = LoadThemesResult()
    if not directory.is_dir():
        return result

    paths = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in (".yaml", ".yml", ".json")
    )

    for path in paths:
        data, err = _parse_theme_file(path)
        if err or data is None:
            result.errors.append(ThemeLoadError(str(path), err or "unknown error"))
            continue

        name = data.get("name")
        if not name or not isinstance(name, str):
            result.errors.append(ThemeLoadError(str(path), "missing 'name' field"))
            continue

        theme, err = load_theme_from_dict(data)
        if err or theme is None:
            result.errors.append(ThemeLoadError(str(path), err or "unknown error"))
            continue

        # Theme loaded; surface any non-fatal issues (typos, bad values) as warnings.
        for warn in validate_theme_dict(data):
            result.errors.append(ThemeLoadError(str(path), f"warning: {warn}"))

        result.themes[name.lower()] = theme

    return result
