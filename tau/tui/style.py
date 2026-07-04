"""Structured styling: the Style/Modifier/Color layer, mirroring ratatui's ``style`` module.

The rest of the TUI (``utils.py`` SGR constants, ``_AnsiStateTracker``) bakes
style directly into ANSI-laden strings and re-parses it back out for diffing.
This module keeps style as data — ``fg``/``bg``/modifiers stay structured
from the moment a ``Span`` is authored until a ``Cell`` resolves it to SGR at
write time (see ``buffer.py``). Nothing here emits ANSI except ``Style.sgr()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Flag, auto


class _ResetColor:
    """Sentinel: explicitly reset to the terminal's default color.

    Distinct from ``None`` (which means "inherit whatever this patches
    onto" — see ``Style.patch``). Mirrors ratatui's ``Color::Reset``: a
    style can now *force* the default color back on, not just leave it
    untouched. Singleton — use the module-level ``RESET`` instance.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "RESET"


RESET_COLOR = _ResetColor()

# A named ANSI colour, an ``(r, g, b)`` truecolor triple, a 0-255 palette
# index, or RESET_COLOR (explicit reset — see _ResetColor above).
Color = str | tuple[int, int, int] | int | _ResetColor

_NAMED_FG = {
    "black": 30, "red": 31, "green": 32, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37, "default": 39,
    "bright_black": 90, "bright_red": 91, "bright_green": 92, "bright_yellow": 93,
    "bright_blue": 94, "bright_magenta": 95, "bright_cyan": 96, "bright_white": 97,
}
_NAMED_BG = {name: code + 10 for name, code in _NAMED_FG.items()}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(spec: str) -> Color:
    """Parse a color from ``"#rrggbb"``, a named color, ``"reset"``, or a bare palette index.

    Mirrors ratatui's ``Color::from_str`` — for config-driven theming
    (e.g. a theme file specifying colors as plain strings) instead of
    requiring Python code to construct tuples/lookup names by hand.
    """
    text = spec.strip()
    if text.lower() in ("reset", "default"):
        return RESET_COLOR
    m = _HEX_RE.match(text)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if text.lstrip("-").isdigit():
        return int(text)
    normalized = re.sub(r"[-_\s]", "_", text.lower())
    if normalized not in _NAMED_FG:
        raise ValueError(f"unrecognized color {spec!r}")
    return normalized


def _color_sgr(color: Color, *, background: bool) -> str:
    if isinstance(color, _ResetColor):
        return f"\x1b[{49 if background else 39}m"
    table = _NAMED_BG if background else _NAMED_FG
    if isinstance(color, str):
        code = table.get(color.lower())
        if code is None:
            raise ValueError(f"unknown named color {color!r}")
        return f"\x1b[{code}m"
    if isinstance(color, tuple):
        r, g, b = color
        return f"\x1b[{48 if background else 38};2;{r};{g};{b}m"
    return f"\x1b[{48 if background else 38};5;{color}m"


# 16-color palette index for each name — used by underline color, which only
# has an indexed/truecolor SGR form (58;5;n / 58;2;r;g;b), no plain "58;n" form.
_NAMED_INDEX = {
    "black": 0, "red": 1, "green": 2, "yellow": 3,
    "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
    "bright_black": 8, "bright_red": 9, "bright_green": 10, "bright_yellow": 11,
    "bright_blue": 12, "bright_magenta": 13, "bright_cyan": 14, "bright_white": 15,
}


def _underline_color_sgr(color: Color) -> str:
    """SGR 58 (set underline color) — most terminals support this as indexed/truecolor only."""
    if isinstance(color, _ResetColor):
        return "\x1b[59m"
    if isinstance(color, str):
        idx = _NAMED_INDEX.get(color.lower())
        if idx is None:
            raise ValueError(f"unknown named color {color!r}")
        return f"\x1b[58;5;{idx}m"
    if isinstance(color, tuple):
        r, g, b = color
        return f"\x1b[58;2;{r};{g};{b}m"
    return f"\x1b[58;5;{color}m"


class Modifier(Flag):
    NONE = 0
    BOLD = auto()
    DIM = auto()
    ITALIC = auto()
    UNDERLINE = auto()
    BLINK = auto()
    REVERSED = auto()
    STRIKETHROUGH = auto()

    @staticmethod
    def from_name(name: str) -> Modifier:
        """Look up a modifier by name, case-insensitively (for config-driven theming)."""
        try:
            return Modifier[name.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown modifier {name!r}") from None

    @staticmethod
    def parse(spec: str) -> Modifier:
        """Parse a comma-separated spec like ``"bold,italic"`` into combined flags."""
        result = Modifier.NONE
        for part in spec.split(","):
            part = part.strip()
            if part:
                result |= Modifier.from_name(part)
        return result


_MODIFIER_SGR: dict[Modifier, str] = {
    Modifier.BOLD: "1",
    Modifier.DIM: "2",
    Modifier.ITALIC: "3",
    Modifier.UNDERLINE: "4",
    Modifier.BLINK: "5",
    Modifier.REVERSED: "7",
    Modifier.STRIKETHROUGH: "9",
}

RESET = "\x1b[0m"
OSC8_CLOSE = "\x1b]8;;\x1b\\"


@dataclass(frozen=True, slots=True)
class Style:
    """An unresolved style patch: ``None`` fields mean "inherit whatever this patches onto"."""

    fg: Color | None = None
    bg: Color | None = None
    underline_color: Color | None = None
    link: str | None = None
    add_modifier: Modifier = Modifier.NONE
    sub_modifier: Modifier = Modifier.NONE

    def patch(self, other: Style) -> Style:
        """Layer ``other`` on top of ``self`` (``other`` wins where it sets something).

        Mirrors ratatui's ``Style::patch``: colors/link are simple overrides,
        modifiers are bitwise so ``other`` can turn an inherited modifier back
        off via ``sub_modifier`` without needing to know what ``self`` set.
        """
        return Style(
            fg=other.fg if other.fg is not None else self.fg,
            bg=other.bg if other.bg is not None else self.bg,
            underline_color=other.underline_color
            if other.underline_color is not None
            else self.underline_color,
            link=other.link if other.link is not None else self.link,
            add_modifier=(self.add_modifier & ~other.sub_modifier) | other.add_modifier,
            sub_modifier=(self.sub_modifier & ~other.add_modifier) | other.sub_modifier,
        )

    def sgr(self) -> str:
        """Render as an SGR escape sequence (resolved styles only — call after all patching)."""
        codes = [_MODIFIER_SGR[m] for m in _MODIFIER_SGR if m in self.add_modifier]
        out = f"\x1b[{';'.join(codes)}m" if codes else ""
        if self.fg is not None:
            out += _color_sgr(self.fg, background=False)
        if self.bg is not None:
            out += _color_sgr(self.bg, background=True)
        if self.underline_color is not None:
            out += _underline_color_sgr(self.underline_color)
        if self.link:
            out += f"\x1b]8;;{self.link}\x1b\\"
        return out

    # -- fluent builders (mirrors ratatui's Stylize trait) ----------------------

    def with_fg(self, color: Color) -> Style:
        return replace(self, fg=color)

    def with_bg(self, color: Color) -> Style:
        return replace(self, bg=color)

    def with_underline_color(self, color: Color) -> Style:
        return replace(self, underline_color=color)

    def with_link(self, url: str) -> Style:
        return replace(self, link=url)

    def _add(self, modifier: Modifier) -> Style:
        return replace(
            self,
            add_modifier=self.add_modifier | modifier,
            sub_modifier=self.sub_modifier & ~modifier,
        )

    def bold(self) -> Style:
        return self._add(Modifier.BOLD)

    def dim(self) -> Style:
        return self._add(Modifier.DIM)

    def italic(self) -> Style:
        return self._add(Modifier.ITALIC)

    def underline(self) -> Style:
        return self._add(Modifier.UNDERLINE)

    def blink(self) -> Style:
        return self._add(Modifier.BLINK)

    def reversed(self) -> Style:
        return self._add(Modifier.REVERSED)

    def strikethrough(self) -> Style:
        return self._add(Modifier.STRIKETHROUGH)


def style_transition(previous: Style | None, current: Style) -> str:
    """Return escapes that fully transition between two resolved cell styles.

    SGR reset does not terminate an OSC 8 hyperlink. Close an active link
    explicitly before resetting attributes, otherwise later terminal cells
    remain clickable as part of the previous link.
    """
    if previous == current:
        return ""
    out = OSC8_CLOSE if previous is not None and previous.link else ""
    if previous is not None and previous != Style():
        out += RESET
    if current != Style():
        out += current.sgr()
    return out


def apply_style(style: Style, text: str) -> str:
    """Wrap ``text`` in ``style``'s SGR codes, resetting at the end.

    The legacy-theme equivalent of a ``ColorFn`` call (``theme.field(text)``)
    — same call shape, but ``style`` is structured data instead of an opaque
    closure. Returns ``text`` unchanged when ``style`` is the default (no-op)
    ``Style()``, matching a ColorFn that was previously just ``lambda s: s``.
    """
    if style == Style():
        return text
    suffix = OSC8_CLOSE if style.link else ""
    return style.sgr() + text + suffix + RESET


class Stylize:
    """Mixin giving any ``.patch_style(Style) -> Self`` type the same fluent sugar as ``Style``.

    Mirrors ratatui's ``Stylize`` trait, which lets ``"hi".red().bold()``
    work directly on strings/spans instead of requiring a separately
    constructed ``Style``. ``Span``/``Line``/``Text`` mix this in; each only
    has to implement ``patch_style``.
    """

    __slots__ = ()

    def patch_style(self, style: Style):  # noqa: ANN201 - implemented by each subclass
        raise NotImplementedError

    def fg(self, color: Color):
        return self.patch_style(Style(fg=color))

    def bg(self, color: Color):
        return self.patch_style(Style(bg=color))

    def bold(self):
        return self.patch_style(Style().bold())

    def dim(self):
        return self.patch_style(Style().dim())

    def italic(self):
        return self.patch_style(Style().italic())

    def underline(self):
        return self.patch_style(Style().underline())

    def blink(self):
        return self.patch_style(Style().blink())

    def reversed(self):
        return self.patch_style(Style().reversed())

    def strikethrough(self):
        return self.patch_style(Style().strikethrough())
