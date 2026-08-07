"""Every diff surface shares one palette, and it is the edit tool's.

``_render_edit_result`` hardcoded ``GREEN``/``RED`` from ``tau.tui.utils``, so
it was the one diff a theme could not restyle. It also disagreed with the
transcript's ``render_diff``, which used the *bright* pair — two different
looks for the same thing, and the muted one is the diff people actually read.

So the theme's ``diff_added``/``diff_removed`` are the standard pair now, the
edit tool reads them, and the constants survive only as the fallback for a
renderer invoked without a theme.

Background bands stay available but default to off: a band behind a run of
code competes with whatever colouring sits on top of it, and it can only ever
cover the text, since ``ToolRenderOptions`` carries no width for a
``render_result`` callback to pad a row with.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tau.builtins.tools.edit import _render_edit_result
from tau.themes.loader import load_theme_from_dict
from tau.tool.types import ToolRenderOptions
from tau.tui.style import Style
from tau.tui.theme import LayoutTheme
from tau.tui.utils import strip_ansi

_DIFF = """@@ -1,3 +1,2 @@
-def greet(name):
-    return None
+def greet(name: str) -> None:
 
 def main():
"""

_META = {"lines_added": 1, "lines_removed": 2, "diff": _DIFF}

_ADDED = "\x1b[32m"
_REMOVED = "\x1b[31m"


def _render(theme: object | None) -> list[str]:
    return _render_edit_result(
        "Replaced 1 occurrence(s) in app.py",
        ToolRenderOptions(metadata=dict(_META), theme=theme, expanded=True),
    )


def _line(lines: list[str], needle: str) -> str:
    return next(line for line in lines if needle in strip_ansi(line))


def test_added_and_removed_use_the_standard_pair() -> None:
    """Bright reads as alarm across a run of consecutive rows."""
    lines = _render(LayoutTheme())

    assert _ADDED in _line(lines, "+  def greet(name: str)")
    assert _REMOVED in _line(lines, "-  def greet(name):")


def test_no_band_by_default() -> None:
    assert "48;5;" not in "".join(_render(LayoutTheme()))
    assert "\x1b[4" not in "".join(_render(LayoutTheme())).replace("\x1b[49m", "")


def test_colours_come_from_the_theme_not_hardcoded_ansi() -> None:
    theme = LayoutTheme()
    theme.message.diff_added = Style().with_fg("magenta")

    assert "\x1b[35m" in _line(_render(theme), "+  def greet(name: str)")


def test_a_theme_that_opts_into_bands_gets_them() -> None:
    theme = LayoutTheme()
    theme.message.diff_added_bg = Style().with_bg(22)

    assert "\x1b[48;5;22m" in _line(_render(theme), "+  def greet(name: str)")


def test_context_and_summary_are_never_banded() -> None:
    theme = LayoutTheme()
    theme.message.diff_added_bg = Style().with_bg(22)
    theme.message.diff_removed_bg = Style().with_bg(52)
    lines = _render(theme)

    assert "48;5;" not in _line(lines, "def main():")
    assert "48;5;" not in _line(lines, "Added 1 line")


def test_no_theme_falls_back_to_the_plain_constants() -> None:
    """--print, RPC and tests render without a theme; they must still colour."""
    lines = _render(None)

    assert _ADDED in _line(lines, "+  def greet(name: str)")
    assert _REMOVED in _line(lines, "-  def greet(name):")


def test_the_text_is_unchanged_by_any_of_it() -> None:
    assert [strip_ansi(x) for x in _render(LayoutTheme())] == [strip_ansi(x) for x in _render(None)]


class TestThemeLoading:
    def _theme(self, name: str):
        data = yaml.safe_load(Path(f"tau/builtins/themes/{name}.yaml").read_text())
        theme, error = load_theme_from_dict(data)
        assert theme is not None and not error, error
        return theme

    def test_dark_pins_the_standard_pair(self) -> None:
        message = self._theme("dark").message

        assert message.diff_added.fg == "green"
        assert message.diff_removed.fg == "red"

    def test_light_pins_darker_inks(self) -> None:
        """A pale background washes out the terminal-palette green/red."""
        message = self._theme("light").message

        assert message.diff_added.fg == (17, 99, 41)
        assert message.diff_removed.fg == (130, 7, 30)

    def test_no_builtin_theme_ships_a_band(self) -> None:
        for path in sorted(Path("tau/builtins/themes").glob("*.yaml")):
            message = self._theme(path.stem).message
            assert message.diff_added_bg is None, path.stem
            assert message.diff_removed_bg is None, path.stem

    def test_a_theme_can_ask_for_a_band(self) -> None:
        theme, error = load_theme_from_dict({"name": "t", "colors": {"diff_added_bg": "#dafbe1"}})

        assert theme is not None and not error
        assert theme.message.diff_added_bg is not None
        assert theme.message.diff_added_bg.bg == (218, 251, 225)
