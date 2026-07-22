"""FirstRunScreen: step flow, live preview callbacks, commit/skip results."""

from __future__ import annotations

from tau.modes.interactive.components.first_run_screen import FirstRunResult, FirstRunScreen
from tau.tui.input import KeyEvent

from tests.render_helpers import render_cells_to_lines

THEME_OPTIONS = [
    ("auto", "Auto — match the terminal background"),
    ("dark", "Dark"),
    ("light", "Light"),
]


def _make_screen(previews: list[str], commits: list[FirstRunResult | None]) -> FirstRunScreen:
    return FirstRunScreen(THEME_OPTIONS, previews.append, commits.append, theme=None)


def test_theme_step_renders_welcome_and_options():
    screen = _make_screen([], [])
    text = "\n".join(render_cells_to_lines(screen, 80))
    assert "Welcome to τ" in text
    assert "Pick a theme" in text
    assert "Dark" in text
    assert "Esc skip setup" in text
    assert "usage data" not in text
    # The sample-conversation preview block renders on the theme step only.
    assert "Edit(parser.py)" in text


def test_arrow_keys_preview_and_wrap():
    previews: list[str] = []
    screen = _make_screen(previews, [])

    assert screen.handle_input(KeyEvent(key="down"))
    assert screen.handle_input(KeyEvent(key="down"))
    assert previews == ["dark", "light"]

    # Wraps past the end back to the first option.
    assert screen.handle_input(KeyEvent(key="down"))
    assert previews[-1] == "auto"

    assert screen.handle_input(KeyEvent(key="up"))
    assert previews[-1] == "light"


def test_enter_advances_to_telemetry_then_commits():
    commits: list[FirstRunResult | None] = []
    screen = _make_screen([], commits)

    screen.handle_input(KeyEvent(key="down"))  # select "dark"
    screen.handle_input(KeyEvent(key="enter"))
    assert commits == []

    text = "\n".join(render_cells_to_lines(screen, 80))
    assert "Share anonymous usage data?" in text
    assert "Enter finish" in text

    screen.handle_input(KeyEvent(key="down"))  # select "Don't share"
    screen.handle_input(KeyEvent(key="enter"))
    assert commits == [FirstRunResult(theme="dark", share_telemetry=False)]


def test_default_commit_is_first_theme_and_share():
    commits: list[FirstRunResult | None] = []
    screen = _make_screen([], commits)

    screen.handle_input(KeyEvent(key="enter"))
    screen.handle_input(KeyEvent(key="enter"))
    assert commits == [FirstRunResult(theme="auto", share_telemetry=True)]


def test_escape_skips_from_either_step():
    commits: list[FirstRunResult | None] = []
    screen = _make_screen([], commits)
    screen.handle_input(KeyEvent(key="escape"))
    assert commits == [None]

    commits.clear()
    screen = _make_screen([], commits)
    screen.handle_input(KeyEvent(key="enter"))  # telemetry step
    screen.handle_input(KeyEvent(key="escape"))
    assert commits == [None]


def test_telemetry_navigation_does_not_call_preview():
    previews: list[str] = []
    screen = _make_screen(previews, [])
    screen.handle_input(KeyEvent(key="enter"))
    screen.handle_input(KeyEvent(key="down"))
    screen.handle_input(KeyEvent(key="up"))
    assert previews == []
