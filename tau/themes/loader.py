from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from tau.themes.types import LoadThemesResult, ThemeLoadError
from tau.tui.style import Style, parse_color
from tau.tui.theme import (
    InputTheme,
    LayoutTheme,
    MarkdownTheme,
    MessageTheme,
    SelectListTheme,
    SpinnerTheme,
)

# ---------------------------------------------------------------------------
# Color parsing
# ---------------------------------------------------------------------------

# Valid named-color tokens theme authors can use — these map to the terminal's
# own 16-colour palette (not fixed RGB), so a theme written with names adapts
# to the user's terminal colours. Mirrors `tau.tui.style`'s named set; kept as
# its own frozenset here since `_valid_color_value` only needs membership, not
# the color values themselves (`parse_color` resolves those).
_NAMED_COLORS: frozenset[str] = frozenset(
    {
        "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white", "default",
        "bright_black", "bright_red", "bright_green", "bright_yellow",
        "bright_blue", "bright_magenta", "bright_cyan", "bright_white",
    }
)


def _parse_hex(value: str) -> tuple[int, int, int] | None:
    """Parse '#rrggbb' → (r, g, b) or return None."""
    v = value.strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            return int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)
        except ValueError:
            pass
    return None


def _make_style(
    value: Any,
    bold: bool = False,
    italic: bool = False,
    dim: bool = False,
) -> Style | None:
    """
    Convert a JSON color value to a Style.

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
        bold = bold or bool(value.get("bold", False))
        italic = italic or bool(value.get("italic", False))
        dim = dim or bool(value.get("dim", False))

    if not color_str:
        return None

    try:
        fg = parse_color(color_str)
    except ValueError:
        return None

    style = Style(fg=fg)
    if bold:
        style = style.bold()
    if italic:
        style = style.italic()
    if dim:
        style = style.dim()
    return style


def _c(
    colors: dict,
    key: str,
    bold: bool = False,
    italic: bool = False,
    dim: bool = False,
) -> Style | None:
    return _make_style(colors.get(key), bold=bold, italic=italic, dim=dim)


# ---------------------------------------------------------------------------
# Dict → LayoutTheme
# ---------------------------------------------------------------------------


def _resolve_var_map(raw_vars: dict) -> dict[str, str]:
    """Flatten a ``vars`` palette, following references between vars (one var may
    point at another). Cyclic/dangling references resolve to their last value.
    """
    resolved: dict[str, str] = {}
    for key, value in raw_vars.items():
        seen: set[str] = set()
        while isinstance(value, str) and value in raw_vars and value not in seen:
            seen.add(value)
            value = raw_vars[value]
        if isinstance(value, str):
            resolved[key] = value
    return resolved


def _apply_vars(value: Any, var_map: dict[str, str]) -> Any:
    """Substitute a ``vars`` reference inside a color spec (string or ``{"color": …}``)."""
    if isinstance(value, str):
        return var_map.get(value, value)
    if isinstance(value, dict):
        c = value.get("color")
        if isinstance(c, str) and c in var_map:
            return {**value, "color": var_map[c]}
    return value


def load_theme_from_dict(data: dict) -> tuple[LayoutTheme | None, str | None]:
    """
    Parse a validated theme dict into a LayoutTheme.
    Returns (theme, None) on success or (None, error_message) on failure.
    """
    name = data.get("name")
    if not name or not isinstance(name, str):
        return None, "missing 'name' field"

    # Resolve the optional ``vars`` palette: a theme can define named colours once
    # (e.g. {"brand": "#00d7ff"}) and reference them from ``colors`` by name.
    raw_vars = data.get("vars", {})
    var_map = _resolve_var_map(raw_vars) if isinstance(raw_vars, dict) else {}
    colors_in: dict = data.get("colors", {})
    colors: dict = (
        {k: _apply_vars(v, var_map) for k, v in colors_in.items()} if var_map else colors_in
    )
    input_cfg: dict = data.get("input", {})
    spinner_cfg: dict = data.get("spinner", {})

    # Use a default instance to fill in any tokens the file omits
    d = LayoutTheme()

    md = MarkdownTheme(
        heading=_c(colors, "heading", bold=True) or d.message.markdown.heading,
        code_inline=_c(colors, "code_inline") or d.message.markdown.code_inline,
        code_block=_c(colors, "code_block") or d.message.markdown.code_block,
        code_block_border=_c(colors, "code_block_border") or d.message.markdown.code_block_border,
        code_syntax_style=(data.get("code_syntax_style", d.message.markdown.code_syntax_style)),
        quote=_c(colors, "quote", italic=True) or d.message.markdown.quote,
        quote_border=_c(colors, "quote_border") or d.message.markdown.quote_border,
        hr=_c(colors, "hr") or d.message.markdown.hr,
        list_bullet=_c(colors, "list_bullet") or d.message.markdown.list_bullet,
        bold=_c(colors, "bold", bold=True) or d.message.markdown.bold,
        italic=_c(colors, "italic", italic=True) or d.message.markdown.italic,
        strikethrough=_c(colors, "strikethrough") or d.message.markdown.strikethrough,
        link_text=_c(colors, "link_text") or d.message.markdown.link_text,
        link_url=_c(colors, "link_url") or d.message.markdown.link_url,
    )

    msg = MessageTheme(
        you_label=_c(colors, "you_label", bold=True) or d.message.you_label,
        assistant_label=_c(colors, "assistant_label", bold=True) or d.message.assistant_label,
        tool_arrow=_c(colors, "tool_arrow") or d.message.tool_arrow,
        tool_result_ok=_c(colors, "tool_result_ok") or d.message.tool_result_ok,
        tool_result_err=_c(colors, "tool_result_err") or d.message.tool_result_err,
        thinking=_c(colors, "thinking", italic=True) or d.message.thinking,
        error_label=_c(colors, "error_label", bold=True) or d.message.error_label,
        dim=_c(colors, "dim") or d.message.dim,
        stream_cursor=_c(colors, "stream_cursor") or d.message.stream_cursor,
        diff_added=_c(colors, "diff_added") or d.message.diff_added,
        diff_removed=_c(colors, "diff_removed") or d.message.diff_removed,
        diff_context=_c(colors, "diff_context") or d.message.diff_context,
        diff_hunk=_c(colors, "diff_hunk") or d.message.diff_hunk,
        show_thinking=bool(data["show_thinking"])
        if "show_thinking" in data
        else d.message.show_thinking,
        show_tool_calls=bool(data["show_tool_calls"])
        if "show_tool_calls" in data
        else d.message.show_tool_calls,
        show_images=bool(data["show_images"]) if "show_images" in data else d.message.show_images,
        markdown=md,
    )

    raw_frames = spinner_cfg.get("frames")
    spinner = SpinnerTheme(
        frames=raw_frames if isinstance(raw_frames, list) else d.spinner.frames,
        interval_ms=int(spinner_cfg["interval_ms"])
        if "interval_ms" in spinner_cfg
        else d.spinner.interval_ms,
        frame_color=_c(colors, "spinner_frame") or d.spinner.frame_color,
        label_color=_c(colors, "spinner_label") or d.spinner.label_color,
        label_thinking=spinner_cfg.get("label_thinking", d.spinner.label_thinking),
        label_tool_calling=spinner_cfg.get("label_tool_calling", d.spinner.label_tool_calling),
        label_compacting=spinner_cfg.get("label_compacting", d.spinner.label_compacting),
    )

    select = SelectListTheme(
        selected_label=_c(colors, "selected_label", bold=True) or d.select_list.selected_label,
        selected_desc=_c(colors, "selected_desc") or d.select_list.selected_desc,
        normal_label=_c(colors, "normal_label") or d.select_list.normal_label,
        normal_desc=_c(colors, "normal_desc") or d.select_list.normal_desc,
        indicator=_c(colors, "indicator") or d.select_list.indicator,
        empty=_c(colors, "empty") or d.select_list.empty,
        selected_dir=_c(colors, "selected_dir", bold=True) or d.select_list.selected_dir,
        selected_bg=_c(colors, "selected_bg") or d.select_list.selected_bg,
    )

    input_theme = InputTheme(
        prefix=input_cfg.get("prefix", d.input.prefix),
        placeholder=input_cfg.get("placeholder", d.input.placeholder),
    )

    layout = LayoutTheme(
        divider=_c(colors, "divider") or d.divider,
        divider_command=_c(colors, "divider_command") or d.divider_command,
        divider_execute=_c(colors, "divider_execute") or d.divider_execute,
        muted=_c(colors, "muted") or d.muted,
        emphasis=_c(colors, "emphasis", bold=True) or d.emphasis,
        success=_c(colors, "success") or d.success,
        error=_c(colors, "error", bold=True) or d.error,
        warning=_c(colors, "warning") or d.warning,
        accent=_c(colors, "accent") or d.accent,
        border=_c(colors, "border") or d.border,
        spinner=spinner,
        message=msg,
        input=input_theme,
        select_list=select,
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
        data = yaml.safe_load(text) if suffix in (".yaml", ".yml") else json.loads(text)
    except Exception as exc:
        return None, f"parse error: {exc}"

    if not isinstance(data, dict):
        return None, "expected a mapping at top level"
    return data, None


# Keys the loader actually reads. Used to warn theme authors about typos.
_VALID_TOP_KEYS = frozenset(
    {
        "name",
        "vars",
        "colors",
        "input",
        "spinner",
        "show_thinking",
        "show_tool_calls",
        "show_images",
        "code_syntax_style",
    }
)
_VALID_COLOR_KEYS = frozenset(
    {
        "assistant_label",
        "bold",
        "code_block",
        "code_block_border",
        "code_inline",
        "accent",
        "border",
        "diff_added",
        "diff_context",
        "diff_hunk",
        "diff_removed",
        "dim",
        "divider",
        "divider_command",
        "divider_execute",
        "emphasis",
        "empty",
        "error",
        "error_label",
        "heading",
        "hr",
        "indicator",
        "muted",
        "success",
        "warning",
        "italic",
        "link_text",
        "link_url",
        "list_bullet",
        "normal_desc",
        "normal_label",
        "quote",
        "quote_border",
        "selected_bg",
        "selected_desc",
        "selected_dir",
        "selected_label",
        "spinner_frame",
        "spinner_label",
        "stream_cursor",
        "strikethrough",
        "thinking",
        "tool_arrow",
        "tool_result_err",
        "tool_result_ok",
        "you_label",
    }
)


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

    raw_vars = data.get("vars", {})
    var_map = _resolve_var_map(raw_vars) if isinstance(raw_vars, dict) else {}
    if isinstance(raw_vars, dict):
        for vkey, vval in raw_vars.items():
            if not _valid_color_value(_apply_vars(vval, var_map)):
                warnings.append(f"invalid var value for '{vkey}': {vval!r}")

    colors = data.get("colors", {})
    if isinstance(colors, dict):
        for key, val in colors.items():
            if key not in _VALID_COLOR_KEYS:
                warnings.append(f"unknown color '{key}'")
            elif not _valid_color_value(_apply_vars(val, var_map)):
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

    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in (".yaml", ".yml", ".json"))

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
