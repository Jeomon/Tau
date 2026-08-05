"""Every site that drops the cell frozen cache must drop the string one too.

MessageList keeps two stores of the same content with separate bookkeeping:
``_frozen_buf`` (cells) and ``_frozen_lines`` (strings). Three places
invalidate on a structural change — an undo reaching past the frozen boundary,
``clear()``, and ``prepend_blocks()`` (session resume, branch navigation,
backfill of older history) — and each originally reset only the cell side.

Left unfixed, the string renderer keeps serving rows for blocks that no longer
exist: stale history after an undo, the old conversation surviving a clear,
duplicated messages after a resume. Same class of bug as the newly-frozen-rows
one — state that only goes wrong *between* frames.
"""

from __future__ import annotations

from tau.message.types import TextContent, UserMessage
from tau.modes.interactive.components.message_list import MessageBlock, MessageList

WIDTH = 40


def _block(text: str) -> MessageBlock:
    b = MessageBlock(UserMessage(contents=[TextContent(content=text)]))
    b.finalize()
    return b


def _text_of(ml: MessageList) -> str:
    from tau.tui.utils import strip_ansi

    return "\n".join(strip_ansi(line) for line in ml.render(WIDTH))


def test_clear_drops_the_string_cache() -> None:
    ml = MessageList()
    for i in range(4):
        ml.add_block(_block(f"message {i}"))
    assert "message 2" in _text_of(ml)

    ml.clear()

    assert ml.render(WIDTH) == []
    assert ml._frozen_lines == []


def test_prepend_blocks_does_not_duplicate_history() -> None:
    ml = MessageList()
    for i in range(3):
        ml.add_block(_block(f"recent {i}"))
    _text_of(ml)  # freeze them

    ml.prepend_blocks([_block("older 0"), _block("older 1")])

    out = _text_of(ml)
    assert out.index("older 0") < out.index("recent 0")
    for marker in ("older 0", "older 1", "recent 0", "recent 1", "recent 2"):
        assert out.count(marker) == 1, f"{marker} appears {out.count(marker)} times"


def test_prepend_matches_a_list_built_in_that_order() -> None:
    prepended = MessageList()
    for i in range(3):
        prepended.add_block(_block(f"recent {i}"))
    _text_of(prepended)
    prepended.prepend_blocks([_block("older 0"), _block("older 1")])

    direct = MessageList()
    for t in ("older 0", "older 1", "recent 0", "recent 1", "recent 2"):
        direct.add_block(_block(t))

    assert _text_of(prepended) == _text_of(direct)


def test_undo_past_the_frozen_boundary_drops_stale_rows() -> None:
    ml = MessageList()
    for i in range(4):
        ml.add_block(_block(f"message {i}"))
    _text_of(ml)  # freeze all four

    # Pop past the frozen boundary — what _guard_frozen_bounds exists for.
    ml._blocks.pop()
    ml._blocks.pop()
    ml._guard_frozen_bounds()

    out = _text_of(ml)
    assert "message 3" not in out
    assert "message 2" not in out
    assert "message 1" in out


def test_remove_last_then_render_has_no_stale_row() -> None:
    ml = MessageList()
    ml.add_block(_block("kept"))
    ml.add_block(_block("undone"))
    assert "undone" in _text_of(ml)

    ml.remove_last()

    out = _text_of(ml)
    assert "undone" not in out
    assert "kept" in out


def test_reset_line_cache_clears_every_field() -> None:
    """A partially reset cache is worse than none — it serves wrong offsets."""
    ml = MessageList()
    for i in range(3):
        ml.add_block(_block(f"m{i}"))
    ml.render(WIDTH)
    assert ml._frozen_lines

    ml._reset_line_cache()

    assert ml._frozen_lines == []
    assert ml._lines_block_count == 0
    assert ml._lines_unit_ends == []
    assert ml._lines_unit_rows == []
    assert ml._lines_width == -1
