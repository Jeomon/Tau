"""An attachment that cannot be drawn is framed, not dropped at the margin.

Audio, video and file blocks have always rendered through ``_shell_line`` — the
same '└' connector tool results and notifications use. Images only did so when
the *theme* switched images off. When the theme allowed them but the terminal
could not draw them (no kitty/iTerm2 protocol), ``Image.render`` returned bare
fallback text, which landed flush against the left margin:

    ❯ [image #1][image #2]
      the second image colour code is the one we need
    [Image: [image/jpeg] 710x556]
    [Image: [image/jpeg] 1094x524]

reading as stray output rather than as part of the message that carries it.
"""

from __future__ import annotations

import base64
import io

import pytest

from tau.message.types import ImageContent, TextContent, UserMessage
from tau.modes.interactive.components.message_list import MessageBlock
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi


def _png(width: int = 8, height: int = 4) -> str:
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def _no_inline_images(monkeypatch):
    """A terminal with no image protocol — Terminal.app, tmux, screen, VS Code."""
    from tau.tui.terminal import TerminalCapabilities

    monkeypatch.setattr(
        "tau.tui.terminal.get_capabilities",
        lambda: TerminalCapabilities(images=None, truecolor=True, hyperlinks=False),
    )


def _render(theme: MessageTheme | None = None) -> list[str]:
    message = UserMessage(
        contents=[TextContent(content="look at this"), ImageContent(images=[_png()])]
    )
    block = MessageBlock(message, theme=theme or MessageTheme())
    return [strip_ansi(line) for line in block.render(80)]


def _attachment(lines: list[str]) -> str:
    return next(line for line in lines if "[Image:" in line)


def test_an_undrawable_image_is_framed(_no_inline_images) -> None:
    assert _attachment(_render()).lstrip().startswith("└ ")


def test_it_is_indented_like_every_other_placeholder(_no_inline_images) -> None:
    """Same column as a tool result's '└', so a message reads as one block."""
    line = _attachment(_render())

    assert line.startswith("    └ "), line


def test_the_text_itself_is_unchanged(_no_inline_images) -> None:
    assert "[Image: [image/png] 8x4]" in _attachment(_render())


def test_the_message_text_still_renders(_no_inline_images) -> None:
    assert any("look at this" in line for line in _render())


def test_images_off_in_the_theme_is_framed_too() -> None:
    """The pre-existing path; it must not regress while widening the condition."""
    theme = MessageTheme()
    theme.show_images = False

    assert _attachment(_render(theme)).startswith("    └ ")


def test_a_drawable_terminal_still_gets_the_real_image(monkeypatch) -> None:
    """Framing must not swallow the escape sequence on a capable terminal."""
    from tau.tui.terminal import TerminalCapabilities

    monkeypatch.setattr(
        "tau.tui.terminal.get_capabilities",
        lambda: TerminalCapabilities(images="kitty", truecolor=True, hyperlinks=True),
    )
    message = UserMessage(contents=[ImageContent(images=[_png()])])
    rendered = "".join(MessageBlock(message, theme=MessageTheme()).render(80))

    assert "\x1b_G" in rendered, "the kitty escape was replaced by a placeholder"
    assert "[Image:" not in rendered
