"""Syntax highlighting must never cost a frame.

`_highlight_code` promised in its docstring that highlighting failing "for any
reason" falls back to plain rendering. Its `try` covered only the `highlight`
call — `_lexer` and `_pygments`, which do the deferred imports, sat outside it.
So an import failure propagated through `render_markdown` and out of
`TUI._do_render`, which catches it and abandons the whole frame: the screen
simply did not update.

The failure is real, not hypothetical. Four times in one session's log:

    ImportError: cannot import name 'Terminal256Formatter'
                 from 'pygments.formatters'

pygments populates `formatters` lazily, and importing it from the event loop
while an extension imports it on a worker thread can observe it half-built.
`lru_cache` does not memoise exceptions, so every attempt retried and dropped
another frame instead of degrading once.
"""

from __future__ import annotations

import pytest

import tau.tui.markdown as markdown
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi

_FENCED = "Here:\n\n```python\nx = 1\n```\n\ndone."


@pytest.fixture
def _clear_caches():
    """Drop memoised pygments lookups either side of a test.

    The originals are captured up front: a test monkeypatches these names, and
    re-reading them at teardown would find the stand-in, which has no
    ``cache_clear``. Clearing afterwards matters because ``lru_cache`` caches a
    *successful* lookup, so one test's real formatter would otherwise leak into
    the next test's simulated failure.
    """
    caches = (markdown._pygments, markdown._lexer, markdown._formatter)
    for cache in caches:
        cache.cache_clear()
    yield
    for cache in caches:
        cache.cache_clear()


def _render() -> list[str]:
    return [
        strip_ansi(line).rstrip()
        for line in markdown.render_markdown(_FENCED, 40, MessageTheme().markdown)
    ]


@pytest.mark.parametrize(
    ("name", "exc"),
    [
        (
            "the import failure from the log",
            ImportError("cannot import name 'Terminal256Formatter'"),
        ),
        ("pygments missing entirely", ModuleNotFoundError("No module named 'pygments'")),
        ("an unexpected failure", RuntimeError("pygments exploded")),
    ],
)
def test_a_broken_highlighter_still_renders_the_block(
    monkeypatch, _clear_caches, name: str, exc: Exception
) -> None:
    def boom():
        raise exc

    monkeypatch.setattr(markdown, "_pygments", boom)

    lines = _render()

    assert "x = 1" in " ".join(lines), f"{name}: the code block was lost"
    assert "done." in " ".join(lines), f"{name}: content after the block was lost"


def test_a_broken_lexer_lookup_is_survivable(monkeypatch, _clear_caches) -> None:
    """_lexer sits outside the old guard too."""

    def boom(_lang):
        raise ImportError("half-built pygments")

    monkeypatch.setattr(markdown, "_lexer", boom)

    assert "x = 1" in " ".join(_render())


def test_a_broken_formatter_is_survivable(monkeypatch, _clear_caches) -> None:
    def boom(_style):
        raise ImportError("half-built pygments")

    monkeypatch.setattr(markdown, "_formatter", boom)

    assert "x = 1" in " ".join(_render())


def test_highlighting_still_works_when_pygments_is_fine(_clear_caches) -> None:
    """The fallback must not have disabled highlighting outright."""
    highlighted = markdown._highlight_code("x = 1", "python", "monokai")

    assert highlighted is not None
    assert any("\x1b[" in line for line in highlighted)


def test_an_unknown_language_still_falls_back_quietly(_clear_caches) -> None:
    assert markdown._highlight_code("x = 1", "not-a-language", "monokai") is None


def test_no_language_or_style_short_circuits(_clear_caches) -> None:
    assert markdown._highlight_code("x = 1", "", "monokai") is None
    assert markdown._highlight_code("x = 1", "python", "") is None
